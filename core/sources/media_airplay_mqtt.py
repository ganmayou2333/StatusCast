"""AirPlay / Shairport Sync / MQTT media source placeholder.

This class exists to prove the source boundary: the core and adapters depend on
``MediaSource``, not on AMS. A later implementation will subscribe to the MQTT
metadata topic published by Shairport Sync and map it to MediaStatus.

It intentionally reports a degraded status instead of raising, so switching
``media.source`` in config never takes the daemon down.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.models import MediaStatus
from core.sources.base import MediaSource


class AirPlayMqttMediaSource(MediaSource):
    name = "media-airplay-mqtt"

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        topic: str = "shairport-sync/metadata",
        retry_interval: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.topic = topic
        self.retry_interval = retry_interval
        self._error = "AirPlay/MQTT 备选源尚未实现"

    def snapshot(self) -> MediaStatus:
        return MediaStatus(error=self._error)

    async def run(self, stop_event: asyncio.Event) -> None:
        # Keep the same lifecycle contract as AmsMediaSource. This stub waits
        # until shutdown; the real implementation will connect to MQTT here.
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self.retry_interval
                )
            except TimeoutError:
                pass

    async def close(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "topic": self.topic,
            "error": self._error,
        }
