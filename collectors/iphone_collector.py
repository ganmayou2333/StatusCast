"""iPhone collector plugin (USB first, then optional Wi-Fi / LAN transports).

The stable channel is ``pymobiledevice3`` over USB. Two optional fallbacks can
be enabled from the ``IPhone`` config section:

1. ``"wifiDataChannel": true`` -- try the experimental Wi-Fi data channel
   (usbmuxd ``Network`` first, then Bonjour/mobdev2). A battery read there is a
   full, non-degraded result (``transport.active == "usb-wifi"``).
2. ``"ip": "192.168.1.20"`` with ``"fallback"`` not disabled -- ARP/ping the
   configured address. That only proves liveness, so the entry is *degraded*
   (battery/media/model null, ``error`` stays ``None``); the next step only
   happens when every richer channel failed, and the existing
   ``CollectorError`` text is preserved when no fallback applies.

The Wi-Fi step runs inside one ``asyncio.run`` loop with a total 3 s budget
(``asyncio.wait_for`` around both discovery paths), so a sleeping or absent
device can never stall a refresh.
"""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Any

from .base import BaseCollector, CollectorError
from .lan_probe import as_bool, make_transport, probe_lan

# Whole Wi-Fi data-channel attempt (usbmuxd Network + Bonjour) must fit here.
WIFI_DATA_CHANNEL_TIMEOUT_SECONDS = 3.0


@dataclass
class _IPhoneStatus:
    battery_level: int | None = None
    is_charging: bool | None = None
    media_title: str | None = None
    media_artist: str | None = None
    media_state: str = "unknown"
    device_model: str | None = None
    transport_detail: str | None = None


class IPhoneCollector(BaseCollector):
    """Read iPhone battery and model from USB, falling back when configured."""

    platform = "ios"
    device_id = "iphone"

    def __init__(self, entry: dict[str, Any] | None = None) -> None:
        entry = entry if isinstance(entry, dict) else {}
        self.ip = str(entry.get("ip") or "").strip() or None
        if self.ip:
            try:
                socket.inet_aton(self.ip)
            except OSError as exc:
                raise CollectorError(f"iPhone IP 非法：{self.ip}") from exc
        self.fallback = as_bool(entry.get("fallback"), True)
        self.wifi_data_channel = as_bool(entry.get("wifiDataChannel"), False)

    def get_data(self) -> dict[str, Any]:
        now = int(time.time())
        status: _IPhoneStatus | None = None
        error: CollectorError | None = None

        try:
            status = asyncio.run(self._read())
            if status.battery_level is None and status.device_model is None:
                status = None
                raise CollectorError("未检测到 iPhone（USB 未连接或设备未解锁）")
        except CollectorError as exc:
            error = exc
        except Exception as exc:
            error = CollectorError(f"iPhone 采集失败：{exc}")

        if status is not None:
            return self._payload(status, "usb", now)

        # Step 2: experimental Wi-Fi data channel. Only a battery read counts
        # as success; a handshake without battery data is not enough.
        if self.wifi_data_channel:
            wifi = self._read_wifi()
            if wifi is not None and wifi.battery_level is not None:
                return self._payload(wifi, "usb-wifi", now)

        # Step 3: liveness-only LAN fallback.
        if self.ip and self.fallback:
            online, method, _mac = probe_lan(self.ip)
            return self._degraded_payload(online, method, now)

        if error is None:
            error = CollectorError("未检测到 iPhone（USB 未连接或设备未解锁）")
        raise error

    # -- payload helpers -------------------------------------------------

    def _payload(
        self, status: _IPhoneStatus, active: str, now: int
    ) -> dict[str, Any]:
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": status.device_model,
            "batteryLevel": status.battery_level,
            "isCharging": status.is_charging,
            "mediaTitle": status.media_title,
            "mediaArtist": status.media_artist,
            "mediaState": status.media_state,
            "foregroundApp": None,
            "foregroundAppName": None,
            "transport": make_transport(
                active,
                degraded=False,
                online=True,
                last_seen=now,
                detail=status.transport_detail or active,
            ),
            "error": None,
        }

    def _degraded_payload(
        self, online: bool, method: str, now: int
    ) -> dict[str, Any]:
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

    # -- USB -------------------------------------------------------------

    async def _read(self) -> _IPhoneStatus:
        # Imported lazily so a missing pymobiledevice3 only disables iPhone
        # collection instead of breaking the whole dispatcher.
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.diagnostics import DiagnosticsService

        status = _IPhoneStatus()

        try:
            async with await create_using_usbmux(connection_type="USB") as lockdown:
                status.device_model = (lockdown.all_values or {}).get("ProductType")
                async with DiagnosticsService(lockdown=lockdown) as diagnostics:
                    info = await diagnostics.get_battery()
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(f"未检测到 iPhone：{exc}") from exc

        self._apply_battery(status, info)
        # Media placeholder; AMS will land here once BLE pairing is available.
        return status

    # -- Wi-Fi data channel ---------------------------------------------

    def _read_wifi(self) -> _IPhoneStatus | None:
        """Best-effort synchronous wrapper; never raises."""
        try:
            return asyncio.run(self._read_wifi_async())
        except Exception:
            return None

    async def _read_wifi_async(self) -> _IPhoneStatus | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WIFI_DATA_CHANNEL_TIMEOUT_SECONDS
        best: _IPhoneStatus | None = None

        try:
            first = await asyncio.wait_for(
                self._read_wifi_usbmux(),
                timeout=WIFI_DATA_CHANNEL_TIMEOUT_SECONDS,
            )
        except Exception:
            first = None
        if first is not None:
            if first.battery_level is not None:
                return first
            best = first

        remaining = deadline - loop.time()
        if remaining > 0:
            try:
                second = await asyncio.wait_for(
                    self._read_wifi_bonjour(remaining), timeout=remaining
                )
            except Exception:
                second = None
            if second is not None:
                if second.battery_level is not None:
                    return second
                best = second or best
        return best

    async def _read_wifi_usbmux(self) -> _IPhoneStatus | None:
        from pymobiledevice3.lockdown import create_using_usbmux

        lockdown = await create_using_usbmux(
            connection_type="Network", autopair=False
        )
        async with lockdown:
            return await self._read_lockdown(lockdown, "wifi:usbmux")

    async def _read_wifi_bonjour(self, timeout: float) -> _IPhoneStatus | None:
        from pymobiledevice3.lockdown import get_mobdev2_lockdowns

        async for ip, lockdown in get_mobdev2_lockdowns(
            only_paired=True, timeout=timeout
        ):
            try:
                return await self._read_lockdown(lockdown, f"wifi:bonjour:{ip}")
            finally:
                try:
                    await lockdown.close()
                except Exception:
                    pass
        return None

    async def _read_lockdown(
        self, lockdown: Any, detail: str
    ) -> _IPhoneStatus:
        """Handshake result from a Wi-Fi lockdown connection.

        Metadata usually arrives on handshake; the battery service is allowed
        to fail (locked/asleep device) and leaves ``battery_level`` ``None`` so
        the caller can fall through to the LAN step.
        """
        from pymobiledevice3.services.diagnostics import DiagnosticsService

        status = _IPhoneStatus(transport_detail=detail)
        status.device_model = (
            getattr(lockdown, "all_values", None) or {}
        ).get("ProductType")
        try:
            async with DiagnosticsService(lockdown=lockdown) as diagnostics:
                info = await diagnostics.get_battery()
        except Exception:
            return status
        self._apply_battery(status, info)
        return status

    @staticmethod
    def _apply_battery(status: _IPhoneStatus, info: dict[str, Any]) -> None:
        level = info.get("BatteryCurrentCapacity", info.get("CurrentCapacity"))
        charging = info.get("BatteryIsCharging", info.get("IsCharging"))
        if level is not None:
            status.battery_level = int(level)
        if charging is not None:
            if isinstance(charging, str):
                status.is_charging = charging.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
            else:
                status.is_charging = bool(charging)
