"""LAN reachability probes and the shared ``transport`` object.

This module owns the stdlib-only network primitives that used to live in
``console_collector``: the Windows ARP probe, the ICMP ping fallback and a TCP
port probe. Nothing here raises for an unreachable host -- probes return typed
"not reachable" results so callers can fall through their channel chains.

It also builds the additive ``transport`` dict every device entry may carry:

    {"active": "usb", "degraded": False, "online": True,
     "lastSeenAt": 1793458293, "detail": "usb"}

``active`` names the channel that actually produced this snapshot; ``degraded``
means only liveness (not the platform's full payload) was available. ``detail``
is free-form and never contains credentials.
"""

from __future__ import annotations

import ctypes
import os
import socket
import struct
import subprocess
from typing import Any, Iterable

# Default TCP ports probed for consoles when ``tcpPorts`` is not configured.
# 9295 = PS5 remote play, 3074 = Xbox live, 80/443 = generic web UI.
DEFAULT_TCP_PORTS: tuple[int, ...] = (9295, 3074, 80, 443)

# Per-port connect budget for the console TCP fallback.
TCP_PROBE_TIMEOUT_SECONDS = 0.3

# Hard cap on distinct configured TCP ports, so a bad config cannot make one
# console probe take unbounded time.
MAX_TCP_PORTS = 8


def make_transport(
    active: str,
    *,
    degraded: bool = False,
    online: bool = True,
    last_seen: int | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Build the additive ``transport`` block exposed to consumers."""
    return {
        "active": str(active),
        "degraded": bool(degraded),
        "online": bool(online),
        "lastSeenAt": int(last_seen) if last_seen is not None else None,
        "detail": str(detail) if detail is not None else None,
    }


def as_bool(value: Any, default: bool) -> bool:
    """Best-effort boolean config parsing; unknown values keep ``default``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def sanitize_ports(
    value: Any, default: Iterable[int] = DEFAULT_TCP_PORTS
) -> tuple[int, ...]:
    """Clean a configured port list: ints only, 1..65535, deduped, max 8.

    A non-list value means "not configured" and falls back to the defaults. A
    list is honoured as given (after cleaning); an empty list disables the TCP
    fallback entirely.
    """
    if not isinstance(value, (list, tuple)):
        return tuple(default)
    ports: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            port = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
        if len(ports) >= MAX_TCP_PORTS:
            break
    return tuple(ports)


def arp_probe(ip: str) -> tuple[bool, str | None]:
    """ARP the address. Returns (reachable, mac-or-None); never raises."""
    if os.name != "nt":
        return False, None
    try:
        send_arp = ctypes.windll.iphlpapi.SendARP
        send_arp.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        send_arp.restype = ctypes.c_ulong
        mac_len = ctypes.c_ulong(6)
        buf = ctypes.create_string_buffer(6)
        dest = struct.unpack("!I", socket.inet_aton(ip))[0]
        ret = send_arp(dest, 0, buf, ctypes.byref(mac_len))
        if ret == 0:
            mac = ":".join(f"{byte:02x}" for byte in buf.raw[: max(0, mac_len.value)])
            return True, mac or None
        return False, None
    except Exception:
        return False, None


def ping_probe(ip: str, timeout: float = 2.0) -> bool:
    """ICMP ping fallback (``ping`` exit code 0 with a TTL= reply)."""
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", ip],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and "TTL=" in (result.stdout or "").upper()


def tcp_probe(
    ip: str, ports: Iterable[int], timeout: float = TCP_PROBE_TIMEOUT_SECONDS
) -> tuple[bool, int | None]:
    """Try TCP connects in order; return (any-open, first-open-port)."""
    for port in ports:
        try:
            with socket.create_connection((ip, int(port)), timeout=timeout):
                return True, int(port)
        except Exception:
            continue
    return False, None


def probe_lan(ip: str) -> tuple[bool, str, str | None]:
    """ARP then ICMP ping. Returns ``(online, method, mac)``; never raises.

    ``method`` is ``"arp"`` / ``"ping"`` on success and ``"lan"`` when both
    probes miss (the caller decides whether to keep probing other channels).
    """
    reached, mac = arp_probe(ip)
    if reached:
        return True, "arp", mac
    if ping_probe(ip):
        return True, "ping", None
    return False, "lan", None
