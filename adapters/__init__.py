"""StatusCast adapters: push a Status object to a display target."""

from adapters.base import Adapter
from adapters.nekotrak_adapter import NekoTrakAdapter
from adapters.snapshot_adapter import SnapshotAdapter
from adapters.stdout_adapter import StdoutAdapter

__all__ = ["Adapter", "NekoTrakAdapter", "SnapshotAdapter", "StdoutAdapter"]
