"""NekoTrak adapter placeholder.

NekoTrak is just one display adapter. Its real transport (PowerShell expression,
local HTTP call, or file handoff) will be implemented after the core is stable.
Keeping this class separate prevents NekoTrak-specific formatting or transport
from leaking into the core.
"""

from __future__ import annotations

import logging

from adapters.base import Adapter
from core.models import Status


class NekoTrakAdapter(Adapter):
    name = "nekotrak"

    def __init__(self) -> None:
        self.last_status: Status | None = None
        self._logger = logging.getLogger(__name__)

    async def push(self, status: Status) -> None:
        self.last_status = status
        self._logger.debug("NekoTrak adapter placeholder received: %s", status.display_text())
