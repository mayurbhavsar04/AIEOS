from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from aieos.adapters.persistence_postgres.outbox import decode_event, encode_event
from aieos.contracts.events import EventEnvelope, EventMetadata
from aieos_api.settings import HostSettings, RuntimeAdapter


def test_event_storage_round_trip_preserves_frozen_envelope() -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    event = EventEnvelope(
        event_id="event-1",
        event_type="ExecutionAttemptSucceeded",
        event_version="1.0",
        occurred_at=now,
        recorded_at=now,
        producer="Skill Runtime",
        correlation_id="correlation-1",
        subject="execution-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        payload={"result_id": "result-1"},
        metadata=EventMetadata(trace_id="trace-1"),
    )
    assert decode_event(encode_event(event)) == event


def test_postgres_requires_database_url() -> None:
    with pytest.raises(ValueError, match="database_url"):
        HostSettings(runtime_adapter=RuntimeAdapter.POSTGRES)


def test_safe_summary_never_contains_database_url() -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr("postgresql+asyncpg://user:secret@localhost/aieos"),
    )
    assert "secret" not in str(settings.safe_summary())
