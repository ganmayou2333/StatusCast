"""Combine source snapshots into the single Status object."""

from __future__ import annotations

import time

from core.models import BatteryStatus, MediaStatus, Status


class StatusAggregator:
    """Merge source states and collect degradation information.

    The aggregator deliberately knows nothing about USB, BLE or adapters.
    """

    def __init__(self, battery_ttl: float = 45.0) -> None:
        self.battery_ttl = battery_ttl

    def build(self, battery: BatteryStatus, media: MediaStatus) -> Status:
        errors: list[str] = []
        if battery.error:
            errors.append(f"battery: {battery.error}")
        elif (
            battery.updated_at is not None
            and time.time() - battery.updated_at > self.battery_ttl
        ):
            errors.append("battery: 数据过期")

        if media.error:
            errors.append(f"media: {media.error}")

        return Status(
            battery=battery,
            media=media,
            generated_at=time.time(),
            errors=errors,
        )
