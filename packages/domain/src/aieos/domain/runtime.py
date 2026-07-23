"""Pure time and identity ports used by authoritative runtime owners."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4


class Clock(Protocol):
    """Return timezone-aware UTC instants."""

    def now(self) -> datetime: ...


class IdentifierFactory(Protocol):
    """Create opaque identifiers with a stable diagnostic prefix."""

    def new(self, prefix: str) -> str: ...


class SystemClock:
    """Production clock implementation based on the standard library."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdentifierFactory:
    """Production opaque identifier factory."""

    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


__all__ = ("Clock", "IdentifierFactory", "SystemClock", "UuidIdentifierFactory")
