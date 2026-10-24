"""Collector plugin contract for StatusCast's Python data layer.

Each collector owns one device platform and returns a plain ``dict`` in the
same shape used by ``run/status.json``:

    {
        "id": "iphone",
        "platform": "ios",
        "model": "iPhone15,3",
        "batteryLevel": 87,
        "isCharging": True,
        "mediaTitle": "Yesterday",
        "mediaArtist": "The Beatles",
        "mediaState": "playing",
        "foregroundApp": None,
        "foregroundAppName": None,
        "error": None,
    }

Collectors raise :class:`CollectorError` when their device is unavailable. The
main dispatcher catches it and emits a normal device entry with ``error`` set,
so one missing device never takes down the whole snapshot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CollectorError(RuntimeError):
    """Raised when a collector cannot read its device."""


class BaseCollector(ABC):
    """Base class for pluggable status collectors."""

    platform: str = "unknown"
    device_id: str = "unknown"

    @abstractmethod
    def get_data(self) -> dict[str, Any]:
        """Return a status dict, or raise :class:`CollectorError`."""

    def error_payload(self, message: str) -> dict[str, Any]:
        """Build the standard device entry used when collection fails."""
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": None,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "error": message or "采集失败",
        }
