"""Owned events-only transport and producer outbox ports."""

from typing import Protocol

from aieos.contracts.events import EventMessage


class EventConsumer(Protocol):
    """Idempotent consumer of immutable Event facts."""

    async def consume(self, event: EventMessage) -> None: ...


class EventBus(Protocol):
    """Publish immutable Events; Commands are intentionally unrepresentable."""

    async def publish(self, event: EventMessage) -> None: ...


class EventOutbox(Protocol):
    """Record authoritative Events and publish pending delivery intents."""

    def record(self, event: EventMessage) -> None: ...

    async def drain(self) -> int: ...


__all__ = ("EventBus", "EventConsumer", "EventOutbox")
