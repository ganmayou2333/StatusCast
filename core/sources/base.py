"""Abstract source interfaces."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from core.models import BatteryStatus, MediaStatus


class BatterySource(ABC):
    """A polled source of battery information."""

    name = "battery"

    @abstractmethod
    async def read(self) -> BatteryStatus:
        """Return the latest battery reading; never raise for a transient failure."""

    async def close(self) -> None:
        """Release any resources held by the source."""


class MediaSource(ABC):
    """A long-running source of media information.

    Media sources own a background lifecycle: ``run`` keeps the connection and
    updates internal state until ``stop_event`` is set. Adapters read the latest
    snapshot via ``snapshot``.
    """

    name = "media"

    @abstractmethod
    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until ``stop_event`` is set."""

    @abstractmethod
    def snapshot(self) -> MediaStatus:
        """Return the latest media state without blocking."""

    async def close(self) -> None:
        """Release any resources held by the source."""
