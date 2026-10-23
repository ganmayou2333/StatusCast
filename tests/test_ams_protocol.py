from __future__ import annotations

import unittest

from core.models import PlaybackState
from core.sources.ams_protocol import (
    PLAYER,
    PLAYER_PLAYBACK_INFO,
    TRACK,
    TRACK_ARTIST,
    TRACK_TITLE,
    AmsMediaState,
    parse_entity_update,
    parse_playback_info,
)


class AmsProtocolTests(unittest.TestCase):
    def test_parse_entity_update(self) -> None:
        raw = bytes([TRACK, TRACK_TITLE, 0]) + "Hello".encode()
        note = parse_entity_update(raw)
        self.assertEqual(note.entity, TRACK)
        self.assertEqual(note.attribute, TRACK_TITLE)
        self.assertEqual(note.value, "Hello")
        self.assertFalse(note.truncated)
        self.assertEqual(note.name, "Track.Title")

    def test_truncated_flag(self) -> None:
        note = parse_entity_update(bytes([2, 2, 1]) + b"Long...")
        self.assertTrue(note.truncated)

    def test_invalid_short_payload(self) -> None:
        with self.assertRaises(ValueError):
            parse_entity_update(b"\x00\x01")

    def test_playback_info(self) -> None:
        self.assertEqual(parse_playback_info("1,1.0,42.5"), (1, 1.0, 42.5))
        self.assertEqual(parse_playback_info("bad"), (None, 0.0, 0.0))

    def test_state_aggregation(self) -> None:
        state = AmsMediaState()
        state.apply(
            parse_entity_update(
                bytes([PLAYER, PLAYER_PLAYBACK_INFO, 0]) + b"1,1.0,12.5"
            )
        )
        state.apply(
            parse_entity_update(bytes([TRACK, TRACK_ARTIST, 0]) + b"Artist")
        )
        state.apply(
            parse_entity_update(bytes([TRACK, TRACK_TITLE, 0]) + b"Title")
        )
        status = state.to_status()
        self.assertEqual(status.title, "Title")
        self.assertEqual(status.artist, "Artist")
        self.assertEqual(status.playback_state, PlaybackState.PLAYING)
        self.assertEqual(status.elapsed_seconds, 12.5)


if __name__ == "__main__":
    unittest.main()
