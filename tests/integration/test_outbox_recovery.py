"""Crash-recovery semantics for the local shared outbox state."""

from datetime import UTC, datetime

import pytest

from aieos.adapters.event_bus_in_process import (
    InMemoryOutboxStore,
    InProcessEventBus,
    OutboxRelay,
)
from aieos.contracts.events import EventEnvelope, EventMetadata


class RecordingConsumer:
    def __init__(self) -> None:
        self.event_ids: list[str] = []

    async def consume(self, event: EventEnvelope) -> None:
        self.event_ids.append(event.event_id)


@pytest.mark.anyio
async def test_reconstructed_relay_recovers_recorded_pending_event() -> None:
    store = InMemoryOutboxStore()
    bus = InProcessEventBus()
    consumer = RecordingConsumer()
    bus.subscribe("ReferenceRecorded", "recorder", consumer)
    now = datetime(2026, 7, 23, tzinfo=UTC)
    event = EventEnvelope(
        event_id="event-pending",
        event_type="ReferenceRecorded",
        event_version="1.0",
        occurred_at=now,
        recorded_at=now,
        producer="Reference Test",
        tenant_id="tenant",
        workspace_id="workspace",
        correlation_id="correlation",
        causation_id="decision",
        subject="subject",
        payload={},
        metadata=EventMetadata(),
    )
    interrupted_relay = OutboxRelay(store, bus)
    interrupted_relay.record(event)
    store.claim(event.event_id)

    recovered_relay = OutboxRelay(store, bus)
    assert await recovered_relay.drain() == 1
    assert consumer.event_ids == ["event-pending"]
    assert await recovered_relay.drain() == 0
