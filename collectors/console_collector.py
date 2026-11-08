"""Console collector plugin (C1 · L0 LAN probe, no credentials).

Game consoles are probed over the local network: an ARP request first
(``iphlpapi.SendARP`` via ctypes, same subnet, no admin rights), then an
ICMP ``ping`` fallback, and finally -- unless ``"fallback": false`` -- a TCP
connect sweep over ``tcpPorts`` (default ``[9295, 3074, 80, 443]``). ARP/ping
are implemented in :mod:`collectors.lan_probe` and re-exported here so existing
callers and tests keep patching ``console_collector.arp_probe``. L0 cannot tell
"idle" from "playing", so every entry carries ``console.idleHint`` and no media
fields.

The daemon starts a fresh Python process on every refresh, so the hysteresis
fail streak lives in ``run/console_state.json``: a console is marked offline
only after ``FAILS_TO_OFFLINE`` (2, the firmware's ``SC_FAILS_TO_OFFLINE``)
consecutive failed probes; one success is online again. A console that was
never seen online is offline immediately. The persisted record stays bounded:
the streak is clamped at that limit, and when the collector is handed the ids
the current config declares (``live_ids``) records of consoles that were
removed from the configuration are pruned on write. Offline is reported as a
normal entry with ``error`` always ``None`` and a human-facing
``console.statusText`` (``offlineText`` from the config entry, defaulting to
"离线"); only invalid configuration raises :class:`CollectorError`.

The dispatcher probes every configured console in parallel threads, and each
collector process may probe them all at once, so the whole read-compute-write
cycle on the shared state file is guarded by a module-level lock. Without it
two consoles finishing at the same time would load the same snapshot and the
last writer would silently drop the other device's fail streak.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .base import BaseCollector, CollectorError
from .lan_probe import (
    DEFAULT_TCP_PORTS,
    arp_probe,
    as_bool,
    make_transport,
    ping_probe,
    probe_lan,
    sanitize_ports,
    tcp_probe,
)

STATE_FILE = Path(__file__).resolve().parents[1] / "run" / "console_state.json"

DEFAULT_OFFLINE_TEXT = "离线"

# Consecutive failed probes that mark a console offline. Same semantics as the
# firmware's SC_FAILS_TO_OFFLINE (firmware/esp32/main/core/sc_core.h); the
# persisted streak is clamped here so the counter cannot grow without bound.
FAILS_TO_OFFLINE = 2

# Fields this collector writes into one state record. A top-level object made
# only of these is a console record (and may be pruned); anything else in the
# file belongs to someone else and is preserved.
RECORD_FIELDS = frozenset({"ip", "online", "failStreak", "lastSeenAt"})

# Serialises load -> update -> save of the shared console state file.
_STATE_LOCK = threading.Lock()

__all__ = [
    "ConsoleCollector",
    "STATE_FILE",
    "DEFAULT_OFFLINE_TEXT",
    "FAILS_TO_OFFLINE",
    "DEFAULT_TCP_PORTS",
    "arp_probe",
    "ping_probe",
    "tcp_probe",
    "probe_lan",
    "make_transport",
    "clamp_fail_streak",
    "prune_state",
    "load_state",
    "save_state",
]


def load_state(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".tmp-{os.getpid()}")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except OSError:
        pass


def clamp_fail_streak(value: Any, reached: bool) -> int:
    """Next fail streak: 0 on a successful probe, else previous + 1, clamped.

    The clamp is what keeps ``run/console_state.json`` bounded: a console that
    has been gone for a month stores ``2``, not one increment per round. A
    stored value that is not a number (corrupt or hand-edited state) counts as a
    fresh streak instead of raising through the whole collector.
    """
    if reached:
        return 0
    try:
        previous = int(value)
    except (TypeError, ValueError):
        previous = 0
    return max(0, min(previous + 1, FAILS_TO_OFFLINE))


def prune_state(state: dict[str, Any], live_ids: Iterable[str]) -> None:
    """Drop records of consoles the current config no longer declares.

    Only top-level objects built purely out of this collector's record fields
    are treated as console records. A key someone else owns (a scalar, an object
    with foreign fields) is preserved, so pruning never deletes state this
    collector does not understand.
    """
    live = {str(device) for device in live_ids}
    for key in list(state):
        if key in live:
            continue
        record = state[key]
        if isinstance(record, dict) and set(record) <= RECORD_FIELDS:
            del state[key]


class ConsoleCollector(BaseCollector):
    platform = "console"

    def __init__(
        self,
        entry: dict[str, Any],
        state_path: Path | None = None,
        now: int | None = None,
        live_ids: Iterable[str] | None = None,
    ) -> None:
        device_id = str(entry.get("id") or "").strip()
        ip = str(entry.get("ip") or "").strip()
        if not device_id or not ip:
            raise CollectorError("主机配置缺少 id 或 ip")
        try:
            socket.inet_aton(ip)
        except OSError as exc:
            raise CollectorError(f"主机 IP 非法：{ip}") from exc
        self.device_id = device_id
        self.ip = ip
        self.name = str(entry.get("name") or device_id).strip() or device_id
        self.type = str(entry.get("type") or "console").strip() or "console"
        self.offline_text = str(entry.get("offlineText") or "").strip() or None
        self.tcp_ports = sanitize_ports(entry.get("tcpPorts"))
        self.fallback = as_bool(entry.get("fallback"), True)
        self.state_path = Path(state_path) if state_path else STATE_FILE
        # Console ids the current config declares. ``None`` means "the caller did
        # not say", which keeps every record instead of guessing; the dispatcher
        # always passes the config's list so removed consoles get pruned.
        self.live_ids = (
            frozenset(str(device) for device in live_ids)
            if live_ids is not None
            else None
        )
        self._now = now
        self._probe_detail: str | None = None

    def probe(self) -> tuple[bool, str, str | None]:
        """ARP -> ping -> (optional) TCP. Returns (online, method, mac).

        Uses the module-level probe names (not ``lan_probe`` internals) so
        callers and tests can patch ``console_collector.arp_probe`` etc.
        """
        online, mac = arp_probe(self.ip)
        if online:
            self._probe_detail = "arp"
            return True, "arp", mac
        if ping_probe(self.ip):
            self._probe_detail = "ping"
            return True, "ping", None
        if self.fallback and self.tcp_ports:
            hit, port = tcp_probe(self.ip, self.tcp_ports)
            if hit:
                self._probe_detail = f"tcp:{port}" if port is not None else "tcp"
                return True, "tcp", None
        self._probe_detail = None
        return False, "lan", None

    def get_data(self) -> dict[str, Any]:
        reached, method, mac = self.probe()
        now = int(self._now if self._now is not None else time.time())

        # One atomic load-update-save so parallel collectors cannot overwrite
        # each other's failStreak / lastSeenAt records.
        with _STATE_LOCK:
            state = load_state(self.state_path)
            record = state.get(self.device_id)
            # A hand-edited or half-written record must not take the collector
            # down: anything that is not an object reads as an empty record.
            record = record if isinstance(record, dict) else {}
            fail_streak = clamp_fail_streak(record.get("failStreak", 0), reached)
            previously_online = bool(record.get("online", False))
            last_seen = now if reached else record.get("lastSeenAt")
            is_online = reached or (
                previously_online and fail_streak < FAILS_TO_OFFLINE
            )
            state[self.device_id] = {
                "ip": self.ip,
                "online": is_online,
                "failStreak": fail_streak,
                "lastSeenAt": last_seen,
            }
            if self.live_ids is not None:
                # Prune on write, never on read: the file is shared by every
                # console of this round, so the ids still in config are exactly
                # the records worth keeping. The device we just wrote is always
                # kept, even if the caller's list lags behind the entry.
                prune_state(state, self.live_ids | {self.device_id})
            save_state(self.state_path, state)

        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": self.name,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "console": {
                "online": bool(is_online),
                "method": method if reached else "lan",
                "lastSeenAt": int(last_seen) if last_seen else None,
                "mac": mac,
                "idleHint": True,
                "name": self.name,
                "type": self.type,
                "statusText": None
                if is_online
                else (self.offline_text or DEFAULT_OFFLINE_TEXT),
            },
            "transport": make_transport(
                method if reached else "none",
                degraded=False,
                online=bool(is_online),
                last_seen=int(last_seen) if last_seen else None,
                detail=self._probe_detail,
            ),
            "error": None,
        }
