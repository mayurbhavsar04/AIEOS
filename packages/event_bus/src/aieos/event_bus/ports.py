"""Owned events-only transport port."""

from typing import Protocol

from aieos.contracts.events import EventEnvelope


class EventBus(Protocol):
    """Publish immutable Events; Commands are intentionally unrepresentable."""

    async def publish(self, event: EventEnvelope) -> None: ...


__all__ = ("EventBus",)
