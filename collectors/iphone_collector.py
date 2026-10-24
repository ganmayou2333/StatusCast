"""iPhone collector plugin (USB / pymobiledevice3)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .base import BaseCollector, CollectorError


@dataclass
class _IPhoneStatus:
    battery_level: int | None = None
    is_charging: bool | None = None
    media_title: str | None = None
    media_artist: str | None = None
    media_state: str = "unknown"
    device_model: str | None = None


class IPhoneCollector(BaseCollector):
    """Read iPhone battery and model from the local USB connection."""

    platform = "ios"
    device_id = "iphone"

    def get_data(self) -> dict[str, Any]:
        try:
            status = asyncio.run(self._read())
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(f"iPhone 采集失败：{exc}") from exc

        if status.battery_level is None and status.device_model is None:
            raise CollectorError("未检测到 iPhone（USB 未连接或设备未解锁）")

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
            "error": None,
        }

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

        # Media placeholder; AMS will land here once BLE pairing is available.
        return status
