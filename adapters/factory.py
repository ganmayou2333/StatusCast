"""Adapter composition.

New adapters should be added here (and in config.toml) rather than in the
daemon. The daemon only knows the Adapter interface.
"""

from __future__ import annotations

from adapters.base import Adapter
from adapters.nekotrak_adapter import NekoTrakAdapter
from adapters.snapshot_adapter import SnapshotAdapter
from adapters.stdout_adapter import StdoutAdapter
from core.config import Config


def build_adapters(config: Config) -> list[Adapter]:
    adapters: list[Adapter] = []
    if config.stdout.enabled:
        adapters.append(StdoutAdapter())
    if config.snapshot.enabled:
        path = config.resolve_path(config.snapshot.path or "run/status.json")
        adapters.append(SnapshotAdapter(path))
    if config.nekotrak.enabled:
        adapters.append(NekoTrakAdapter())
    return adapters
