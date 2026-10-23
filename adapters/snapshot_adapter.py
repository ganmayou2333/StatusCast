"""Write the latest Status to a JSON snapshot for a thin client.

PowerShell should read this file instead of starting a new Python/BLE session on
every refresh. Writes are atomic (temp file + os.replace), so a reader never
observes a half-written JSON document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from adapters.base import Adapter
from core.models import Status


class SnapshotAdapter(Adapter):
    name = "snapshot"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def push(self, status: Status) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, **status.to_dict()}
        temp_path = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}"
        )
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, self.path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    async def close(self) -> None:
        return None
