#!/usr/bin/env python3
import asyncio
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dataclasses import dataclass

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.diagnostics import DiagnosticsService


@dataclass
class Status:
    battery_level: int | None = None
    is_charging: bool | None = None
    media_title: str | None = None
    media_artist: str | None = None
    media_state: str = "unknown"
    device_model: str | None = None


class BatterySource:
    """USB battery source, reusing the validated get_battery.py approach."""

    async def fetch(self, status: Status) -> Status:
        try:
            async with await create_using_usbmux(
                connection_type="USB"
            ) as lockdown:
                status.device_model = (lockdown.all_values or {}).get(
                    "ProductType"
                )
                async with DiagnosticsService(lockdown=lockdown) as diagnostics:
                    info = await diagnostics.get_battery()

            level = info.get(
                "BatteryCurrentCapacity", info.get("CurrentCapacity")
            )
            charging = info.get("BatteryIsCharging", info.get("IsCharging"))
            if level is None or charging is None:
                return status

            status.battery_level = int(level)
            if isinstance(charging, str):
                status.is_charging = charging.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
            else:
                status.is_charging = bool(charging)
        except Exception:
            pass
        return status


class MediaSource:
    """Music source placeholder.

    AMS 待蓝牙硬件就绪后接入。
    """

    def fetch(self, status: Status) -> Status:
        return status


async def main() -> int:
    status = Status()
    status = await BatterySource().fetch(status)
    status = MediaSource().fetch(status)

    battery = "NA" if status.battery_level is None else str(status.battery_level)
    charging = (
        "NA" if status.is_charging is None else str(status.is_charging).lower()
    )
    title = status.media_title or "NA"
    artist = status.media_artist or "NA"
    state = status.media_state or "unknown"
    model = status.device_model or "NA"

    print(
        f"STATUS:BATTERY:{battery}|CHARGING:{charging}"
        f"|MEDIA:{title}|{artist}|{state}|MODEL:{model}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
