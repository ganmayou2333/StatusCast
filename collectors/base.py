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
        return error_entry(self.platform, self.device_id, message or "采集失败")


def error_entry(
    platform: str,
    device_id: str,
    message: str,
) -> dict[str, Any]:
    """Standard schema-3 device entry for a failed/config-invalid device.

    This is the single place that builds the "nothing was collected" shape, so
    the dispatcher's invalid-config entries and ``BaseCollector.error_payload``
    cannot drift apart. The field set is asserted against
    ``schema/status-contract.json`` by the test suite.
    """
    return {
        "id": device_id,
        "platform": platform,
        "model": None,
        "batteryLevel": None,
        "isCharging": None,
        "mediaTitle": None,
        "mediaArtist": None,
        "mediaState": "unknown",
        "foregroundApp": None,
        "foregroundAppName": None,
        # No channel produced this snapshot, so expose the same transport
        # shape every other entry carries (`active="none"`, offline).
        "transport": {
            "active": "none",
            "degraded": False,
            "online": False,
            "lastSeenAt": None,
            "detail": None,
        },
        "error": message or "采集失败",
    }

