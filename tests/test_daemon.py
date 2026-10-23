from __future__ import annotations

import asyncio
import logging
import time
import unittest

from adapters.base import Adapter
from core.config import Config
from core.daemon import Daemon
from core.models import BatteryStatus, MediaStatus, Status
from core.sources.base import BatterySource, MediaSource


class FakeBattery(BatterySource):
    name = "fake-battery"

    def __init__(self) -> None:
        self.reads = 0
        self.closed = False

    async def read(self) -> BatteryStatus:
        self.reads += 1
        return BatteryStatus(level=70, is_charging=True, updated_at=time.time())

    async def close(self) -> None:
        self.closed = True


class FakeMedia(MediaSource):
    name = "fake-media"

    def __init__(self) -> None:
        self.started = False
        self.closed = False

    async def run(self, stop_event: asyncio.Event) -> None:
        self.started = True
        await stop_event.wait()

    def snapshot(self) -> MediaStatus:
        return MediaStatus(title="Song", artist="Artist")

    async def close(self) -> None:
        self.closed = True


class RecordingAdapter(Adapter):
    name = "recording"

    def __init__(self) -> None:
        self.statuses: list[Status] = []
        self.closed = False

    async def push(self, status: Status) -> None:
        self.statuses.append(status)

    async def close(self) -> None:
        self.closed = True


class RaisingAdapter(Adapter):
    name = "raising"

    async def push(self, status: Status) -> None:
        raise RuntimeError("boom")


class DaemonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    async def test_once_pushes_one_status_and_closes_resources(self) -> None:
        battery = FakeBattery()
        adapter = RecordingAdapter()
        daemon = Daemon(
            Config(),
            battery=battery,
            media=None,
            adapters=[adapter],
            once=True,
        )
        exit_code = await daemon.run()
        self.assertEqual(exit_code, 0)
        self.assertEqual(battery.reads, 1)
        self.assertTrue(battery.closed)
        self.assertTrue(adapter.closed)
        self.assertEqual(len(adapter.statuses), 1)
        self.assertEqual(
            adapter.statuses[0].display_text(),
            "🔋70%⚡ | 🎵 不可用",
        )

    async def test_adapter_failure_does_not_block_other_adapters(self) -> None:
        recording = RecordingAdapter()
        daemon = Daemon(
            Config(),
            battery=FakeBattery(),
            media=None,
            adapters=[RaisingAdapter(), recording],
            once=True,
        )
        await daemon.run()
        self.assertEqual(len(recording.statuses), 1)

    async def test_disabled_battery_is_reported(self) -> None:
        adapter = RecordingAdapter()
        daemon = Daemon(
            Config(),
            battery=None,
            media=None,
            adapters=[adapter],
            once=True,
        )
        await daemon.run()
        status = adapter.statuses[0]
        self.assertIsNone(status.battery.level)
        self.assertIn("battery: 电池源未启用", status.errors)

    async def test_media_task_is_started_and_cancelled(self) -> None:
        media = FakeMedia()
        daemon = Daemon(
            Config(),
            battery=FakeBattery(),
            media=media,
            adapters=[],
            once=True,
        )
        await daemon.run()
        self.assertTrue(media.started)
        self.assertTrue(media.closed)

    async def test_from_config_builds_real_components(self) -> None:
        config = Config()
        config.battery.enabled = False
        config.media.source = "none"
        config.stdout.enabled = False
        config.snapshot.enabled = False
        daemon = Daemon.from_config(config, once=True)
        self.assertIsNone(daemon.battery)
        self.assertIsNone(daemon.media)
        self.assertEqual(daemon.adapters, [])


if __name__ == "__main__":
    unittest.main()
