"""Events-only local transport with restart-recoverable shared outbox state."""

from __future__ import annotations

from dataclasses import dataclass

from aieos.contracts.events import EventEnvelope
from aieos.event_bus import EventConsumer


class InProcessEventBus:
    """Deliver immutable Events to zero or more idempotent consumers."""

    def __init__(self) -> None:
        self._consumers: dict[str, list[tuple[str, EventConsumer]]] = {}
        self._delivered: set[tuple[str, str]] = set()
        self.published: list[EventEnvelope] = []

    def subscribe(self, event_type: str, consumer_name: str, consumer: EventConsumer) -> None:
        registrations = self._consumers.setdefault(event_type, [])
        if any(name == consumer_name for name, _ in registrations):
            raise ValueError(f"duplicate Event consumer: {consumer_name}")
        registrations.append((consumer_name, consumer))

    async def publish(self, event: EventEnvelope) -> None:
        if not event.event_id or not event.producer:
            raise ValueError("Event transport envelope is incomplete")
        if all(item.event_id != event.event_id for item in self.published):
            self.published.append(event)
        for consumer_name, consumer in self._consumers.get(event.event_type, []):
            receipt = (event.event_id, consumer_name)
            if receipt in self._delivered:
                continue
            await consumer.consume(event)
            self._delivered.add(receipt)


@dataclass(slots=True)
class _OutboxEntry:
    event: EventEnvelope
    published: bool = False
    claimed: bool = False


class InMemoryOutboxStore:
    """Shared authoritative recording state retained across relay reconstruction."""

    def __init__(self) -> None:
        self._entries: dict[str, _OutboxEntry] = {}

    def record(self, event: EventEnvelope) -> None:
        existing = self._entries.get(event.event_id)
        if existing is not None and existing.event != event:
            raise ValueError("EventId cannot be reused with changed content")
        self._entries.setdefault(event.event_id, _OutboxEntry(event))

    def pending(self) -> tuple[_OutboxEntry, ...]:
        return tuple(
            entry for entry in self._entries.values() if not entry.published and not entry.claimed
        )

    def claim(self, event_id: str) -> None:
        self._entries[event_id].claimed = True

    def mark_published(self, event_id: str) -> None:
        entry = self._entries[event_id]
        entry.published = True
        entry.claimed = False

    def release(self, event_id: str) -> None:
        self._entries[event_id].claimed = False

    def recover_unpublished_claims(self) -> None:
        """Release claims left behind by an interrupted relay instance."""
        for entry in self._entries.values():
            if not entry.published:
                entry.claimed = False


class OutboxRelay:
    """Record producer Events and recover pending delivery after relay restart."""

    def __init__(self, store: InMemoryOutboxStore, event_bus: InProcessEventBus) -> None:
        self._store = store
        self._event_bus = event_bus
        self._store.recover_unpublished_claims()

    def record(self, event: EventEnvelope) -> None:
        self._store.record(event)

    async def drain(self) -> int:
        delivered = 0
        for entry in self._store.pending():
            self._store.claim(entry.event.event_id)
            try:
                await self._event_bus.publish(entry.event)
            except Exception:
                self._store.release(entry.event.event_id)
                raise
            else:
                self._store.mark_published(entry.event.event_id)
                delivered += 1
        return delivered


__all__ = ("InMemoryOutboxStore", "InProcessEventBus", "OutboxRelay")
