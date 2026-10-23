from __future__ import annotations

import time
import unittest

from core.aggregator import StatusAggregator
from core.models import BatteryStatus, MediaStatus


class AggregatorTests(unittest.TestCase):
    def test_healthy_sources_have_no_errors(self) -> None:
        status = StatusAggregator().build(
            BatteryStatus(level=80, is_charging=False, updated_at=time.time()),
            MediaStatus(title="Song", updated_at=time.time()),
        )
        self.assertEqual(status.errors, [])
        self.assertEqual(status.display_text(), "🔋80% | 🎵 Song")

    def test_source_errors_are_collected(self) -> None:
        status = StatusAggregator().build(
            BatteryStatus(error="no device"),
            MediaStatus(error="auth required"),
        )
        self.assertEqual(len(status.errors), 2)
        self.assertTrue(status.errors[0].startswith("battery:"))
        self.assertTrue(status.errors[1].startswith("media:"))

    def test_stale_battery_is_reported(self) -> None:
        status = StatusAggregator(battery_ttl=1).build(
            BatteryStatus(level=50, updated_at=time.time() - 10),
            MediaStatus(),
        )
        self.assertIn("battery: 数据过期", status.errors)


if __name__ == "__main__":
    unittest.main()
