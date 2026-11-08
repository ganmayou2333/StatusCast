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

import time
from abc import ABC, abstractmethod
from typing import Any

# ``error`` text for a device the round budget no longer covers. Shared with
# status_core so there is one wording for "we ran out of time" everywhere.
BUDGET_EXHAUSTED_MESSAGE = "采集预算用尽（本轮未完成）"


class CollectorError(RuntimeError):
    """Raised when a collector cannot read its device."""


class BaseCollector(ABC):
    """Base class for pluggable status collectors."""

    platform: str = "unknown"
    device_id: str = "unknown"

    # ``time.monotonic()`` deadline for the current round, assigned by the
    # dispatcher. ``None`` means "no budget", which is what a collector built
    # directly in a test or by a third party gets.
    #
    # Checking this is the difference between bounding when work *starts* and
    # bounding how long it may run: a collector that has already been handed to a
    # worker thread cannot be cancelled, so it has to police itself.
    deadline: float | None = None

    def out_of_budget(self) -> bool:
        """True when this collector has no time left in the round."""
        return self.deadline is not None and time.monotonic() >= self.deadline

    def budget_timeout(self, wanted: float) -> float:
        """Clamp ``wanted`` seconds to what is left of the round budget.

        Raises :class:`CollectorError` when there is nothing left, so callers
        stop instead of starting one more subprocess that cannot finish.
        """
        if self.deadline is None:
            return wanted
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CollectorError(BUDGET_EXHAUSTED_MESSAGE)
        return min(wanted, remaining)

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

