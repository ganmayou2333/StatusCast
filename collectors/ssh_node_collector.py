"""SSH node collector plugin (C2b · Mac / Linux via the Windows OpenSSH client).

One ``ssh`` invocation per refresh runs a POSIX one-liner that prints
``key=value`` lines; the values map onto the standard schema-3 fields
(``batteryLevel`` / ``isCharging`` / ``model``) plus node-local facts such as
``node.uptimeSeconds``. The remote side keeps no resident process and opens no
listening port beyond its own sshd.

``sshKey`` is expanded (``~`` and environment variables) at construction time;
a configured key that resolves to a missing path raises
:class:`CollectorError` immediately, and only the resolved path is ever
mentioned -- never the key material. With a key, ``IdentitiesOnly=yes`` keeps
the client from offering agent keys the server does not expect.

Connection failures raise :class:`CollectorError`, which the dispatcher turns
into a per-device ``error`` entry isolated from every other collector. Unless
``"fallback": false`` is configured, a failed SSH probe is retried as a
liveness-only LAN fallback: the hostname is resolved with
``socket.gethostbyname`` and ARP/ping decides online/offline. That degraded
entry keeps ``error`` ``None`` (``node.ssh == false``, ``node.online`` set,
battery/media/model null); if the name does not resolve, the original SSH error
is re-raised unchanged.
Credentials never appear in error text: ``BatchMode=yes`` auth failures only
print "Permission denied", and stderr is truncated to 160 characters.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Any

from .base import BaseCollector, CollectorError
from .lan_probe import as_bool, make_transport, probe_lan

LINUX_SCRIPT = (
    "c=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1); "
    "s=$(cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -1); "
    "m=$(cat /sys/class/dmi/id/product_name 2>/dev/null); "
    "echo \"battery=$c\"; echo \"charging=$s\"; echo \"model=$m\"; "
    "echo \"hostname=$(uname -n)\"; "
    "echo \"uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)\""
)

MACOS_SCRIPT = (
    "echo \"battery=$(pmset -g batt | grep -E -o '[0-9]+%' | head -1 | tr -d %)\"; "
    "echo \"charging=$(pmset -g batt | grep -E -i -o 'AC attached|discharging|finishing charge|charging|charged|full' | head -1)\"; "
    "echo \"model=$(sysctl -n hw.model 2>/dev/null)\"; "
    "echo \"hostname=$(uname -n)\"; "
    "echo \"boottime=$(sysctl -n kern.boottime | sed -E 's/.*sec = ([0-9]+).*/\\1/')\""
)

SUPPORTED_PLATFORMS = {"linux", "macos"}

_PROBE_TIMEOUT_SECONDS = 8.0


def _to_int(value: str | None) -> int | None:
    text = (value or "").strip()
    return int(text) if text.isdigit() else None


def _parse_charging(value: str | None) -> bool | None:
    """Map a ``pmset -g batt`` status word/phrase onto ``isCharging``.

    Real output variants include ``charging``, ``finishing charge``,
    ``charged``, ``full``, ``discharging`` and ``AC attached; not charging``.
    Order matters: ``discharging`` contains ``charging`` and ``not charging``
    contains ``charging``, so the negative cases are matched first.
    """
    text = (value or "").strip().lower()
    if not text:
        return None
    if "not charging" in text:
        return None
    if "discharging" in text:
        return False
    if "finishing charge" in text:
        return True
    if "charging" in text:
        return True
    if "charged" in text or "full" in text:
        return True
    if "ac attached" in text:
        return None
    return None


def _uptime_seconds(platform: str, values: dict[str, str], now: int) -> int | None:
    if platform == "linux":
        text = (values.get("uptime") or "").strip()
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return None
    boottime = _to_int(values.get("boottime"))
    if boottime is None:
        return None
    return max(0, now - boottime)


class SshNodeCollector(BaseCollector):
    def __init__(self, entry: dict[str, Any], now: int | None = None) -> None:
        device_id = str(entry.get("id") or "").strip()
        user = str(entry.get("user") or "").strip()
        host = str(entry.get("host") or "").strip()
        platform = str(entry.get("platform") or "").strip().lower()
        if not device_id or not user or not host:
            raise CollectorError("节点配置缺少 id/user/host")
        if platform not in SUPPORTED_PLATFORMS:
            raise CollectorError(
                f"节点平台暂不支持：{platform or '(空)'}（当前仅 linux/macos）"
            )
        self.device_id = device_id
        self.platform = platform
        self.user = user
        self.host = host
        raw_key = str(entry.get("sshKey") or "").strip() or None
        self.ssh_key: str | None = None
        if raw_key:
            expanded = os.path.expandvars(os.path.expanduser(raw_key))
            if not os.path.exists(expanded):
                # Only the resolved path is reported; key material never is.
                raise CollectorError(f"SSH 私钥不存在：{expanded}")
            self.ssh_key = expanded
        self.name = str(entry.get("name") or device_id).strip() or device_id
        self.model_hint = str(entry.get("model") or "").strip() or None
        self.fallback = as_bool(entry.get("fallback"), True)
        self._now = now

    def build_command(self) -> list[str]:
        command = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=2",
            "-o", "StrictHostKeyChecking=accept-new",
            "-n",
        ]
        if self.ssh_key:
            command += ["-o", "IdentitiesOnly=yes", "-i", self.ssh_key]
        command += [f"{self.user}@{self.host}",
                    LINUX_SCRIPT if self.platform == "linux" else MACOS_SCRIPT]
        return command

    def _run(self, command: list[str], timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def parse_output(stdout: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in (stdout or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value.strip()
        return values

    def _ssh_values(self) -> dict[str, str]:
        """Run the remote one-liner and return parsed key/value pairs."""
        try:
            result = self._run(self.build_command(), _PROBE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise CollectorError("ssh 连接失败：8 秒超时") from None
        except FileNotFoundError:
            raise CollectorError("ssh 客户端不可用（需要 Windows OpenSSH 客户端）") from None
        except OSError as exc:
            raise CollectorError(f"ssh 启动失败：{exc}") from None

        if result.returncode != 0:
            lines = (result.stderr or "").strip().splitlines()
            detail = (lines[0] if lines else f"exit {result.returncode}")[:160]
            raise CollectorError(f"ssh 连接失败：{detail}")

        return self.parse_output(result.stdout)

    def get_data(self) -> dict[str, Any]:
        now = int(self._now if self._now is not None else time.time())
        try:
            values = self._ssh_values()
        except CollectorError as ssh_error:
            if not self.fallback:
                raise
            return self._lan_fallback(now, ssh_error)

        battery = _to_int(values.get("battery"))
        charging = _parse_charging(values.get("charging"))
        hostname = (values.get("hostname") or "").strip() or None
        model = (
            self.model_hint
            or (values.get("model") or "").strip()
            or hostname
            or self.name
        )
        uptime = _uptime_seconds(self.platform, values, now)

        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": model,
            "batteryLevel": battery,
            "isCharging": charging,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "node": {
                "ssh": True,
                "method": "ssh",
                "source": "ssh",
                "hostname": hostname,
                "lastSeenAt": now,
                "uptimeSeconds": uptime,
            },
            "transport": make_transport(
                "ssh", degraded=False, online=True, last_seen=now, detail="ssh"
            ),
            "error": None,
        }

    def _lan_fallback(
        self, now: int, ssh_error: CollectorError
    ) -> dict[str, Any]:
        """Degraded liveness-only entry after SSH failed.

        The hostname must resolve for ARP/ping to be meaningful; a resolution
        failure re-raises the original SSH error instead of inventing one.
        """
        try:
            ip = socket.gethostbyname(self.host)
        except OSError:
            raise ssh_error from None

        online, method, _mac = probe_lan(ip)
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": self.model_hint or self.name,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "node": {
                "ssh": False,
                "online": bool(online),
                "method": "lan",
                "source": "lan",
                "hostname": None,
                "statusText": None if online else "离线",
                "lastSeenAt": now if online else None,
                "uptimeSeconds": None,
            },
            "transport": make_transport(
                "lan",
                degraded=True,
                online=bool(online),
                last_seen=now if online else None,
                detail=method,
            ),
            "error": None,
        }
