"""Directed Command contract marker types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Bootstrap marker for a directed Command."""

    command_id: str
    target: str


__all__ = ("CommandEnvelope",)
