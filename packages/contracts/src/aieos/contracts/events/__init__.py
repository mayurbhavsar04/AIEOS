"""Immutable Event contract marker types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Bootstrap marker for an immutable Event."""

    event_id: str
    event_type: str


__all__ = ("EventEnvelope",)
