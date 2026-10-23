"""Print the unified status line to stdout.

Useful for manual runs and for the PowerShell direct-call path. In daemon mode
this adapter is usually disabled, because the display target reads a snapshot
instead of holding the daemon's stdout open.
"""

from __future__ import annotations

import sys
from typing import TextIO

from adapters.base import Adapter
from core.models import Status


class StdoutAdapter(Adapter):
    name = "stdout"

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    async def push(self, status: Status) -> None:
        print(status.display_text(), file=self._stream, flush=True)
