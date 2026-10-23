"""Apple Media Service constants and notification parsing.

The module is deliberately free of bleak imports so it can be unit-tested on
any platform and reused by both the standalone probe and the daemon source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.models import MediaStatus, PlaybackState

AMS_SERVICE = "89d3502b-0f36-433a-8ef4-c502ad55f8dc"
AMS_UPDATE = "2f7cabce-808d-411f-9a0c-bb92ba96c102"
AMS_ATTRIBUTE = "c6b2f38c-23ab-46d8-a6ab-a3a870bbd5d7"
AMS_REMOTE_COMMAND = "9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2"

PLAYER = 0
QUEUE = 1
TRACK = 2

PLAYER_NAME = 0
PLAYER_PLAYBACK_INFO = 1
PLAYER_VOLUME = 2

QUEUE_INDEX = 0
QUEUE_COUNT = 1
QUEUE_SHUFFLE_MODE = 2
QUEUE_REPEAT_MODE = 3

TRACK_ARTIST = 0
TRACK_ALBUM = 1
TRACK_TITLE = 2
TRACK_DURATION = 3

PLAYBACK_STATE_NAMES = {
    0: "paused",
    1: "playing",
    2: "rewinding",
    3: "fast_forwarding",
}

_PLAYBACK_STATE_MAP = {
    0: PlaybackState.PAUSED,
    1: PlaybackState.PLAYING,
    2: PlaybackState.REWINDING,
    3: PlaybackState.FAST_FORWARDING,
}

ENTITY_NAMES = {PLAYER: "Player", QUEUE: "Queue", TRACK: "Track"}

ATTRIBUTE_NAMES = {
    (PLAYER, PLAYER_NAME): "Player.Name",
    (PLAYER, PLAYER_PLAYBACK_INFO): "Player.PlaybackInfo",
    (PLAYER, PLAYER_VOLUME): "Player.Volume",
    (QUEUE, QUEUE_INDEX): "Queue.Index",
    (QUEUE, QUEUE_COUNT): "Queue.Count",
    (QUEUE, QUEUE_SHUFFLE_MODE): "Queue.ShuffleMode",
    (QUEUE, QUEUE_REPEAT_MODE): "Queue.RepeatMode",
    (TRACK, TRACK_ARTIST): "Track.Artist",
    (TRACK, TRACK_ALBUM): "Track.Album",
    (TRACK, TRACK_TITLE): "Track.Title",
    (TRACK, TRACK_DURATION): "Track.Duration",
}

PLAYER_REGISTRATION = bytes([PLAYER, PLAYER_NAME, PLAYER_PLAYBACK_INFO, PLAYER_VOLUME])
TRACK_REGISTRATION = bytes(
    [TRACK, TRACK_ARTIST, TRACK_ALBUM, TRACK_TITLE, TRACK_DURATION]
)


@dataclass(frozen=True, slots=True)
class AmsNotification:
    entity: int
    attribute: int
    flags: int
    value: str

    @property
    def truncated(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def name(self) -> str:
        return ATTRIBUTE_NAMES.get(
            (self.entity, self.attribute),
            f"{ENTITY_NAMES.get(self.entity, f'Entity{self.entity}')}.Attr{self.attribute}",
        )


def registration_payload(entity: int, attributes: list[int] | tuple[int, ...]) -> bytes:
    if entity not in ENTITY_NAMES:
        raise ValueError(f"未知 AMS entity: {entity}")
    if not attributes:
        raise ValueError("至少需要一个 attribute")
    return bytes([entity, *attributes])


def parse_entity_update(data: bytes | bytearray) -> AmsNotification:
    raw = bytes(data)
    if len(raw) < 3:
        raise ValueError(f"Entity Update 数据太短: {raw.hex()}")
    return AmsNotification(
        entity=raw[0],
        attribute=raw[1],
        flags=raw[2],
        value=raw[3:].decode("utf-8", errors="replace"),
    )


def parse_playback_info(value: str) -> tuple[int | None, float, float]:
    """Parse ``"state,rate,elapsed"``; invalid fields fall back to safe values."""
    parts = value.split(",")
    state: int | None = None
    rate = 0.0
    elapsed = 0.0
    if len(parts) >= 1:
        try:
            state = int(parts[0])
        except ValueError:
            state = None
    if len(parts) >= 2:
        try:
            rate = float(parts[1])
        except ValueError:
            rate = 0.0
    if len(parts) >= 3:
        try:
            elapsed = float(parts[2])
        except ValueError:
            elapsed = 0.0
    return state, rate, elapsed


@dataclass(slots=True)
class AmsMediaState:
    player_name: str = ""
    playback_state: int | None = None
    playback_rate: float = 0.0
    elapsed: float = 0.0
    volume: float | None = None
    artist: str = ""
    album: str = ""
    title: str = ""
    duration: float | None = None
    updated_at: float = 0.0
    fields: dict[tuple[int, int], str] = field(default_factory=dict)

    def apply(self, note: AmsNotification) -> None:
        self.updated_at = time.time()
        self.fields[(note.entity, note.attribute)] = note.value
        key = (note.entity, note.attribute)
        if key == (PLAYER, PLAYER_NAME):
            self.player_name = note.value
        elif key == (PLAYER, PLAYER_PLAYBACK_INFO):
            state, rate, elapsed = parse_playback_info(note.value)
            self.playback_state = state
            self.playback_rate = rate
            self.elapsed = elapsed
        elif key == (PLAYER, PLAYER_VOLUME):
            try:
                self.volume = float(note.value)
            except ValueError:
                pass
        elif key == (TRACK, TRACK_ARTIST):
            self.artist = note.value
        elif key == (TRACK, TRACK_ALBUM):
            self.album = note.value
        elif key == (TRACK, TRACK_TITLE):
            self.title = note.value
        elif key == (TRACK, TRACK_DURATION):
            try:
                self.duration = float(note.value)
            except ValueError:
                pass

    def to_status(self, error: str | None = None) -> MediaStatus:
        return MediaStatus(
            title=self.title or None,
            artist=self.artist or None,
            album=self.album or None,
            playback_state=_PLAYBACK_STATE_MAP.get(
                self.playback_state, PlaybackState.UNKNOWN
            ),
            duration_seconds=self.duration,
            elapsed_seconds=self.elapsed or None,
            player_name=self.player_name or None,
            updated_at=self.updated_at or None,
            error=error,
        )

    def display_line(self) -> str:
        status = self.to_status()
        if not status.title:
            return "🎵 尚未收到媒体状态"
        icon = "⏸️" if status.playback_state == PlaybackState.PAUSED else "🎵"
        artist = f" - {status.artist}" if status.artist else ""
        return f"{icon} {status.title}{artist}"
