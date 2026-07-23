"""Deterministic test-only identifier source."""

from dataclasses import dataclass


@dataclass(slots=True)
class DeterministicIdentifiers:
    """Generate stable sequential identifiers for tests."""

    prefix: str = "test"
    next_value: int = 1

    def new(self) -> str:
        """Return the next stable identifier."""
        value = f"{self.prefix}-{self.next_value:04d}"
        self.next_value += 1
        return value
