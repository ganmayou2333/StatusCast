"""Shared data model between sources and adapters.

The whole point of this module is that no source or adapter knows about another
source or adapter. Everything crosses the boundary as a ``Status`` object.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlaybackState(str, Enum):
    UNKNOWN = "unknown"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    REWINDING = "rewinding"
    FAST_FORWARDING = "fast_forwarding"


@dataclass(slots=True)
class BatteryStatus:
    level: int | None = None
    is_charging: bool | None = None
    updated_at: float | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.level is not None and self.error is None


@dataclass(slots=True)
class MediaStatus:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    playback_state: PlaybackState = PlaybackState.UNKNOWN
    duration_seconds: float | None = None
    elapsed_seconds: float | None = None
    player_name: str | None = None
    updated_at: float | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.title) and self.error is None


@dataclass(slots=True)
class Status:
    battery: BatteryStatus = field(default_factory=BatteryStatus)
    media: MediaStatus = field(default_factory=MediaStatus)
    generated_at: float = field(default_factory=time.time)
    errors: list[str] = field(default_factory=list)

    def display_text(self) -> str:
        """One-line text consumed by NekoTrak or any stdout adapter."""
        return f"{self.battery_text()} | {self.media_text()}"

    def battery_text(self) -> str:
        battery = self.battery
        if battery.level is None:
            return "🔋--"
        bolt = "⚡" if battery.is_charging else ""
        return f"🔋{battery.level}%{bolt}"

    def media_text(self) -> str:
        media = self.media
        if media.error and not media.title:
            return "🎵 不可用"
        if not media.title:
            return "🎵 未在播放"

        icon = "⏸️" if media.playback_state == PlaybackState.PAUSED else "🎵"
        artist = f" - {media.artist}" if media.artist else ""
        return f"{icon} {media.title}{artist}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "battery": {
                "level": self.battery.level,
                "is_charging": self.battery.is_charging,
                "updated_at": self.battery.updated_at,
                "error": self.battery.error,
            },
            "media": {
                "title": self.media.title,
                "artist": self.media.artist,
                "album": self.media.album,
                "playback_state": self.media.playback_state.value,
                "duration_seconds": self.media.duration_seconds,
                "elapsed_seconds": self.media.elapsed_seconds,
                "player_name": self.media.player_name,
                "updated_at": self.media.updated_at,
                "error": self.media.error,
            },
            "generated_at": self.generated_at,
            "errors": list(self.errors),
            "text": self.display_text(),
        }
