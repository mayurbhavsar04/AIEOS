from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.sql.schema import Table

from aieos.adapters.persistence_postgres.models import (
    DeliveryReceiptRow,
    ExecutionRow,
    WorkflowStepRow,
)
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


@pytest.mark.parametrize(
    "table",
    (WorkflowStepRow.__table__, ExecutionRow.__table__, DeliveryReceiptRow.__table__),
)
def test_authoritative_foreign_keys_include_tenant_and_workspace(table: Table) -> None:
    constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert constraints
    assert all(
        {"tenant_id", "workspace_id"} <= {column.name for column in constraint.columns}
        for constraint in constraints
    )


def test_historical_migration_is_explicit_and_metadata_independent() -> None:
    migration = Path(
        "adapters/persistence_postgres/migrations/versions/20260727_0001_durable_runtime.py"
    )
    source = migration.read_text()
    assert "op.create_table" in source
    assert "op.drop_table" in source
    assert "create_all" not in source
    assert "drop_all" not in source
