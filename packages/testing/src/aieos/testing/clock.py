"""Deterministic test-only clock."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class DeterministicClock:
    """A manually advanced UTC clock for tests."""

    current: datetime

    def now(self) -> datetime:
        """Return the current deterministic instant."""
        if self.current.tzinfo is None:
            raise ValueError("deterministic clock requires a timezone-aware instant")
        return self.current.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        """Advance without sleeping."""
        if delta < timedelta(0):
            raise ValueError("clock cannot move backwards")
        self.current += delta
