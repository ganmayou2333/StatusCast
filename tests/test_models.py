from __future__ import annotations

import unittest

from core.models import BatteryStatus, MediaStatus, PlaybackState, Status


class StatusTests(unittest.TestCase):
    def test_full_status_line(self) -> None:
        status = Status(
            battery=BatteryStatus(level=85, is_charging=True),
            media=MediaStatus(
                title="Song Title",
                artist="Artist",
                playback_state=PlaybackState.PLAYING,
            ),
        )
        self.assertEqual(status.display_text(), "🔋85%⚡ | 🎵 Song Title - Artist")

    def test_not_charging_and_paused(self) -> None:
        status = Status(
            battery=BatteryStatus(level=42, is_charging=False),
            media=MediaStatus(
                title="Song",
                artist="Artist",
                playback_state=PlaybackState.PAUSED,
            ),
        )
        self.assertEqual(status.display_text(), "🔋42% | ⏸️ Song - Artist")

    def test_missing_battery_and_media(self) -> None:
        self.assertEqual(
            Status(
                battery=BatteryStatus(),
                media=MediaStatus(),
            ).display_text(),
            "🔋-- | 🎵 未在播放",
        )

    def test_media_error_without_title(self) -> None:
        status = Status(
            battery=BatteryStatus(level=10, is_charging=False),
            media=MediaStatus(error="AMS 连接失败"),
        )
        self.assertEqual(status.display_text(), "🔋10% | 🎵 不可用")

    def test_to_dict_contains_text_and_enum_value(self) -> None:
        payload = Status(
            battery=BatteryStatus(level=50, is_charging=False),
            media=MediaStatus(title="A", playback_state=PlaybackState.PLAYING),
        ).to_dict()
        self.assertEqual(payload["media"]["playback_state"], "playing")
        self.assertEqual(payload["text"], "🔋50% | 🎵 A")


if __name__ == "__main__":
    unittest.main()
