#!/usr/bin/env python3
import asyncio
import sys

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.diagnostics import DiagnosticsService


async def read_battery():
    async with await create_using_usbmux(connection_type="USB") as lockdown:
        async with DiagnosticsService(lockdown=lockdown) as diagnostics:
            return await diagnostics.get_battery()


async def main() -> int:
    try:
        info = await read_battery()
        level = info.get("BatteryCurrentCapacity", info.get("CurrentCapacity"))
        charging = info.get("BatteryIsCharging", info.get("IsCharging"))
        if level is None or charging is None:
            raise ValueError("battery fields missing")
        level = int(level)

        if isinstance(charging, str):
            charging = charging.strip().lower() in {"1", "true", "yes"}
        else:
            charging = bool(charging)

        print(f"BATTERY:{level}|CHARGING:{str(charging).lower()}")
        return 0
    except Exception:
        print("BATTERY:NA|CHARGING:NA")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
