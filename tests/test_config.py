from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self) -> None:
        config = Config.load("does-not-exist.toml")
        self.assertTrue(config.battery.enabled)
        self.assertEqual(config.media.source, "ams")
        self.assertTrue(config.snapshot.enabled)

    def test_load_custom_file_and_resolve_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(
                "\n".join(
                    [
                        "[app]",
                        "poll_interval = 2.5",
                        "[battery]",
                        "enabled = false",
                        "poll_interval = 30",
                        "[media]",
                        "source = 'none'",
                        "[adapters.snapshot]",
                        "path = 'state/status.json'",
                    ]
                ),
                encoding="utf-8",
            )
            config = Config.load(path)
            self.assertEqual(config.app.poll_interval, 2.5)
            self.assertFalse(config.battery.enabled)
            self.assertEqual(config.media.source, "none")
            resolved = config.resolve_path(config.snapshot.path)
            self.assertEqual(resolved, Path(temp) / "state" / "status.json")

    def test_rejects_unknown_media_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text("[media]\nsource = 'bogus'\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                Config.load(path)

    def test_rejects_non_table_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text("adapters = 1\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                Config.load(path)


if __name__ == "__main__":
    unittest.main()
