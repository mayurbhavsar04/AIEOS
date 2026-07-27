"""Deterministic test-only identifier source."""

from dataclasses import dataclass


@dataclass(slots=True)
class DeterministicIdentifiers:
    """Generate stable sequential identifiers for tests."""

    prefix: str = "test"
    next_value: int = 1

    def new(self, prefix: str | None = None) -> str:
        """Return the next stable identifier."""
        namespace = self.prefix if prefix is None else f"{self.prefix}-{prefix}"
        value = f"{namespace}-{self.next_value:04d}"
        self.next_value += 1
        return value
