"""Owned events-only transport port."""

from typing import Protocol

from aieos.contracts.events import EventMessage


class EventBus(Protocol):
    """Publish immutable Events; Commands are intentionally unrepresentable."""

    async def publish(self, event: EventMessage) -> None: ...


__all__ = ("EventBus",)
