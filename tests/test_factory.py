from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.nekotrak_adapter import NekoTrakAdapter
from adapters.snapshot_adapter import SnapshotAdapter
from adapters.stdout_adapter import StdoutAdapter
from core.config import Config
from core.sources.battery_usb import BatteryUSB
from core.sources.factory import build_battery_source, build_media_source
from core.sources.media_airplay_mqtt import AirPlayMqttMediaSource
from core.sources.media_ams import AmsMediaSource


class SourceFactoryTests(unittest.TestCase):
    def test_battery_source_respects_enabled_flag(self) -> None:
        config = Config()
        self.assertIsInstance(build_battery_source(config), BatteryUSB)
        config.battery.enabled = False
        self.assertIsNone(build_battery_source(config))

    def test_media_source_selection(self) -> None:
        config = Config()
        self.assertIsInstance(build_media_source(config), AmsMediaSource)
        config.media.source = "airplay_mqtt"
        airplay = build_media_source(config)
        self.assertIsInstance(airplay, AirPlayMqttMediaSource)
        self.assertEqual(airplay.host, "127.0.0.1")
        self.assertEqual(airplay.port, 1883)
        config.media.source = "none"
        self.assertIsNone(build_media_source(config))
        config.media.source = "ams"
        config.media.enabled = False
        self.assertIsNone(build_media_source(config))


class AdapterFactoryTests(unittest.TestCase):
    def test_build_adapters_from_config(self) -> None:
        from adapters.factory import build_adapters

        with tempfile.TemporaryDirectory() as temp:
            config = Config(base_dir=Path(temp))
            config.stdout.enabled = True
            config.snapshot.enabled = True
            config.snapshot.path = "status.json"
            config.nekotrak.enabled = True
            adapters = build_adapters(config)
            self.assertEqual(
                [adapter.name for adapter in adapters],
                ["stdout", "snapshot", "nekotrak"],
            )
            self.assertIsInstance(adapters[0], StdoutAdapter)
            self.assertIsInstance(adapters[1], SnapshotAdapter)
            self.assertIsInstance(adapters[2], NekoTrakAdapter)

    def test_no_adapters_enabled(self) -> None:
        from adapters.factory import build_adapters

        config = Config()
        config.stdout.enabled = False
        config.snapshot.enabled = False
        config.nekotrak.enabled = False
        self.assertEqual(build_adapters(config), [])


if __name__ == "__main__":
    unittest.main()
