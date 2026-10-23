from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from adapters.snapshot_adapter import SnapshotAdapter
from core.models import BatteryStatus, MediaStatus, Status


class SnapshotAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_atomic_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "run" / "status.json"
            adapter = SnapshotAdapter(path)
            status = Status(
                battery=BatteryStatus(level=75, is_charging=True),
                media=MediaStatus(title="Song", artist="Artist"),
            )
            await adapter.push(status)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["battery"]["level"], 75)
            self.assertEqual(payload["text"], "🔋75%⚡ | 🎵 Song - Artist")
            self.assertEqual(list(path.parent.glob("*.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
