"""Adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import Status


class Adapter(ABC):
    """Every display target implements ``push`` and receives the same Status."""

    name = "adapter"

    @abstractmethod
    async def push(self, status: Status) -> None:
        """Publish one status snapshot."""

    async def close(self) -> None:
        """Release any resources held by the adapter."""
