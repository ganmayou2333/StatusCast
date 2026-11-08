"""Android collector plugin (ADB, no root required).

The primary channel is ``adb`` (USB or a TCP device the user connected by
hand). When it yields no device the collector walks an ordered fallback chain
configured from the ``Android`` section:

1. ``adb mdns services`` -- discover ``_adb-tls-connect._tcp`` advertisements
   and ``adb connect`` each address.
2. ``ip`` (optional) -- ``adb pair`` first when ``pairPort`` + ``pairCode`` are
   given, then ``adb connect <ip>:<port>``.
3. ``ip`` with ``fallback`` not disabled -- ARP/ping the address and report a
   degraded liveness-only entry (battery/media/model null, ``error`` ``None``).

``pairCode`` is only ever passed as an ``adb`` argv element: it is never logged,
never embedded in error text and never copied into the returned snapshot.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from typing import Any

from .base import BaseCollector, CollectorError
from .lan_probe import as_bool, make_transport, probe_lan

DEFAULT_ADB_PORT = 5555
ADB_MDNS_SERVICE = "_adb-tls-connect._tcp"

# A "host:port" token from an `adb mdns services` row.
_MDNS_ADDRESS_RE = re.compile(r"(\S+:\d+)")


APP_NAMES = {
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.ss.android.ugc.aweme": "抖音",
    "tv.danmaku.bili": "哔哩哔哩",
    "com.netease.cloudmusic": "网易云音乐",
    "com.tencent.qqmusic": "QQ 音乐",
    "com.google.android.youtube": "YouTube",
    "com.android.chrome": "Chrome",
    "com.microsoft.emmx": "Edge",
    "com.spotify.music": "Spotify",
    "com.xiaomi.mihome": "米家",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
    "com.zhihu.android": "知乎",
    "com.sina.weibo": "微博",
    "com.tencent.wework": "企业微信",
    "com.discord": "Discord",
    "com.steam.android": "Steam",
}

PLAYBACK_STATES = {
    "0": "none",
    "1": "stopped",
    "2": "paused",
    "3": "playing",
    "4": "fast_forwarding",
    "5": "rewinding",
    "6": "buffering",
    "7": "error",
}

# Foreground-app fields exposed by `dumpsys`. The activity manager fields are
# preferred because they describe the activity the user actually resumed; the
# window manager's mCurrentFocus is used as a fallback for OEM builds that omit
# or rename the activity fields.
_ACTIVITY_FOREGROUND_RE = re.compile(
    r"(?:topResumedActivity|mResumedActivity)\s*[=:]\s*[^\n]*?"
    r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)
_WINDOW_FOREGROUND_RE = re.compile(
    r"mCurrentFocus\s*=\s*[^\n]*?"
    r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)

# `mCurrentFocus` can briefly point at the notification shade or a system
# dialog; those are not user apps and should not become the foreground app.
_SYSTEM_FOREGROUND_PACKAGES = {"android", "com.android.systemui"}


def parse_mdns_services(output: str | None) -> list[str]:
    """Extract ``host:port`` targets from ``adb mdns services`` output.

    Only ``_adb-tls-connect._tcp`` rows are considered; the address is the last
    ``host:port`` token on the row, which tolerates the extra columns (instance
    name, service type) and unrelated noise lines.
    """
    addresses: list[str] = []
    for line in (output or "").splitlines():
        if ADB_MDNS_SERVICE not in line:
            continue
        matches = _MDNS_ADDRESS_RE.findall(line)
        if not matches:
            continue
        address = matches[-1]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _connect_succeeded(output: str | None) -> bool:
    text = (output or "").lower()
    if "connected" not in text:
        return False
    return "failed" not in text and "cannot" not in text


def _sanitize_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _sanitize_pair_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 0
    return port if 1 <= port <= 65535 else 0


class AndroidCollector(BaseCollector):
    """Read Android battery, model, foreground app and best-effort media."""

    platform = "android"
    device_id = "android"

    def __init__(
        self,
        entry: dict[str, Any] | None = None,
        adb: str | None = None,
        serial: str | None = None,
    ) -> None:
        entry = entry if isinstance(entry, dict) else {}
        self.adb = adb or os.environ.get("ADB", "adb")
        self.serial = serial or os.environ.get("ANDROID_SERIAL")
        self.ip = str(entry.get("ip") or "").strip() or None
        if self.ip:
            try:
                socket.inet_aton(self.ip)
            except OSError as exc:
                raise CollectorError(f"Android IP 非法：{self.ip}") from exc
        self.port = _sanitize_port(entry.get("port"), DEFAULT_ADB_PORT)
        self.pair_port = _sanitize_pair_port(entry.get("pairPort"))
        self.pair_code = str(entry.get("pairCode") or "").strip() or None
        self.fallback = as_bool(entry.get("fallback"), True)

    def get_data(self) -> dict[str, Any]:
        try:
            return self._collect_primary()
        except CollectorError as primary_error:
            error = primary_error

        # 1) mDNS auto-discovery + connect.
        if self._connect_via_mdns():
            try:
                return self._collect_primary()
            except CollectorError:
                pass

        # 2) Explicit ip[:port], pairing first when configured.
        if self.ip and self._connect_via_ip():
            try:
                return self._collect_primary()
            except CollectorError:
                pass

        # 3) Liveness-only LAN fallback.
        if self.ip and self.fallback:
            online, method, _mac = probe_lan(self.ip)
            return self._degraded_entry(online, method)

        raise error

    # -- primary collection ---------------------------------------------

    def _collect_primary(self) -> dict[str, Any]:
        try:
            serial = self._pick_serial()
            if not serial:
                raise CollectorError("未检测到 Android 设备（adb devices 为空）")
            self.serial = serial

            model = self._get_model()
            level, charging = self._get_battery()
            app = self._get_foreground_app()
            if self.out_of_budget():
                # Media is the one optional field left; spending the last seconds
                # on it would cost the round its snapshot entirely.
                title, artist, media_state = None, None, "unknown"
            else:
                title, artist, media_state = self._get_media()
        except CollectorError:
            raise
        except FileNotFoundError as exc:
            raise CollectorError(
                "ADB not found. Install Android Platform Tools and add adb to PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CollectorError("ADB 命令超时，请确认手机已授权 USB 调试") from exc
        except Exception as exc:
            raise CollectorError(f"Android 采集失败：{exc}") from exc

        if level is None and model is None:
            raise CollectorError("未检测到 Android 设备")

        now = int(time.time())
        active = "adb-wifi" if ":" in self.serial else "adb"
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": model,
            "batteryLevel": level,
            "isCharging": charging,
            "mediaTitle": title,
            "mediaArtist": artist,
            "mediaState": media_state,
            "foregroundApp": app,
            "foregroundAppName": APP_NAMES.get(app or ""),
            "transport": make_transport(
                active,
                degraded=False,
                online=True,
                last_seen=now,
                detail=f"adb:{self.serial}",
            ),
            "error": None,
        }

    def _degraded_entry(self, online: bool, method: str) -> dict[str, Any]:
        now = int(time.time())
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": None,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "transport": make_transport(
                "lan",
                degraded=True,
                online=bool(online),
                last_seen=now if online else None,
                detail=method,
            ),
            "error": None,
        }

    # -- adb plumbing ----------------------------------------------------

    def _run_adb(
        self, args: list[str], timeout: int = 8, serial: bool = True
    ) -> str:
        # Clamp to whatever is left of the round. Without this a single
        # _collect_primary() can chain five autodefault timeouts (~46 s) -- more
        # than the daemon's 30 s kill -- and the whole round dies with it,
        # turning every device stale instead of this one.
        timeout = self.budget_timeout(timeout)
        command = [self.adb]
        if serial and self.serial:
            command += ["-s", self.serial]
        command += args
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"adb exit code {result.returncode}"
            )
        return result.stdout

    def _pick_serial(self) -> str | None:
        if self.serial:
            return self.serial
        output = self._run_adb(["devices"])
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
        return None

    # -- fallback chain --------------------------------------------------

    def _connect_via_mdns(self) -> bool:
        try:
            output = self._run_adb(["mdns", "services"], timeout=5, serial=False)
        except Exception:
            return False
        for address in parse_mdns_services(output):
            try:
                result = self._run_adb(
                    ["connect", address], timeout=5, serial=False
                )
            except Exception:
                continue
            if _connect_succeeded(result):
                return True
        return False

    def _connect_via_ip(self) -> bool:
        if not self.ip:
            return False
        if self.pair_port and self.pair_code:
            self._pair()
        try:
            output = self._run_adb(
                ["connect", f"{self.ip}:{self.port}"], timeout=5, serial=False
            )
        except Exception:
            return False
        return _connect_succeeded(output)

    def _pair(self) -> None:
        """Best-effort ``adb pair``; failures are swallowed generically.

        The pair code travels only as an argv element. Nothing here propagates
        an exception (which could carry the argv in its text) or records it.
        """
        try:
            self._run_adb(
                ["pair", f"{self.ip}:{self.pair_port}", self.pair_code],
                timeout=8,
                serial=False,
            )
        except Exception:
            return

    # -- device reads ----------------------------------------------------

    def _get_model(self) -> str | None:
        model = self._run_adb(["shell", "getprop", "ro.product.model"]).strip()
        manufacturer = self._run_adb(
            ["shell", "getprop", "ro.product.manufacturer"]
        ).strip()
        if not model:
            return None
        if manufacturer and manufacturer.lower() not in model.lower():
            return f"{manufacturer} {model}"
        return model

    def _get_battery(self) -> tuple[int | None, bool | None]:
        output = self._run_adb(["shell", "dumpsys", "battery"])
        level_match = re.search(r"^\s*level:\s*(\d+)", output, re.MULTILINE)
        status_match = re.search(r"^\s*status:\s*(\d+)", output, re.MULTILINE)
        level = int(level_match.group(1)) if level_match else None
        status = int(status_match.group(1)) if status_match else None

        powered = False
        for key in ("AC powered", "USB powered", "Wireless powered", "Dock powered"):
            if re.search(rf"^\s*{re.escape(key)}:\s*true", output, re.MULTILINE):
                powered = True
                break

        charging = None
        if status is not None:
            charging = status in (2, 5) or powered
        elif powered:
            charging = True

        return level, charging

    @staticmethod
    def _match_foreground_package(
        output: str | None, pattern: re.Pattern[str]
    ) -> str | None:
        if not output:
            return None
        match = pattern.search(output)
        if not match:
            return None
        package = match.group(1)
        if package in _SYSTEM_FOREGROUND_PACKAGES:
            return None
        return package

    def _get_foreground_app(self) -> str | None:
        """Cross-check two dumpsys views for the foreground package."""
        try:
            output = self._run_adb(["shell", "dumpsys", "activity", "activities"])
            package = self._match_foreground_package(output, _ACTIVITY_FOREGROUND_RE)
            if package:
                return package
        except Exception:
            pass

        try:
            output = self._run_adb(["shell", "dumpsys", "window", "displays"])
            package = self._match_foreground_package(output, _WINDOW_FOREGROUND_RE)
            if package:
                return package
        except Exception:
            pass

        return None

    def _get_media(self) -> tuple[str | None, str | None, str]:
        try:
            output = self._run_adb(["shell", "dumpsys", "media_session"], timeout=6)
        except Exception:
            return None, None, "unknown"

        state = "unknown"
        state_match = re.search(r"state=PlaybackState\s*\{state=(\d+)", output)
        if state_match:
            state = PLAYBACK_STATES.get(state_match.group(1), "unknown")

        title = None
        artist = None
        description = re.search(r"description=(.+)", output)
        if description:
            parts = [part.strip() for part in description.group(1).split(",")]
            if parts and parts[0]:
                title = parts[0]
            if len(parts) > 1 and parts[1] and parts[1] not in {"null", "Unknown"}:
                artist = parts[1]

        return title, artist, state
