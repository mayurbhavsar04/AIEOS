"""Mandatory real-PostgreSQL durability and recovery matrix."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError

from aieos.adapters.persistence_postgres import (
    PostgresDatabase,
    PostgresOutboxStore,
)
from aieos.adapters.persistence_postgres.models import (
    Base,
    CommandIdempotencyRow,
    DecisionEvidenceRow,
    ExecutionRow,
    OutboxEventRow,
    OutcomeRow,
    WorkflowRow,
    WorkflowStepRow,
)
from aieos.contracts import ResultEnvelope, ResultStatus
from aieos.contracts.events import EventEnvelope, EventMetadata
from aieos_api.composition import CompositionRoot, compose
from aieos_api.settings import HostSettings, RuntimeAdapter

pytestmark = [pytest.mark.integration, pytest.mark.postgres_required, pytest.mark.anyio]

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "alembic_version",
    "command_idempotency",
    "decision_evidence",
    "delivery_receipts",
    "executions",
    "memory_records",
    "outbox_events",
    "outcomes",
    "workflow_steps",
    "workflows",
}


def database_url() -> str:
    url = os.environ.get("AIEOS_TEST_DATABASE_URL")
    if url is None:
        if os.environ.get("CI"):
            pytest.fail("mandatory AIEOS_TEST_DATABASE_URL is not configured in CI")
        pytest.skip("live PostgreSQL suite not executed: set AIEOS_TEST_DATABASE_URL")
    return url


async def reset(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        await session.execute(
            text(
                "TRUNCATE delivery_receipts, outbox_events, outcomes, "
                "command_idempotency, executions, workflow_steps, workflows, "
                "decision_evidence, memory_records CASCADE"
            )
        )


def event(
    event_id: str, *, tenant: str = "tenant-1", workspace: str = "workspace-1"
) -> EventEnvelope:
    now = datetime.now(UTC)
    return EventEnvelope(
        event_id=event_id,
        event_type="ExecutionAttemptSucceeded",
        event_version="1.0",
        occurred_at=now,
        recorded_at=now,
        producer="Skill Runtime",
        correlation_id="correlation-1",
        subject="execution-1",
        tenant_id=tenant,
        workspace_id=workspace,
        payload={"result_id": "result-1"},
        metadata=EventMetadata(),
    )


@pytest.fixture
async def database() -> AsyncIterator[PostgresDatabase]:
    value = PostgresDatabase(database_url())
    await reset(value)
    yield value
    await value.close()


async def test_migration_revision_readiness_and_schema_parity(
    database: PostgresDatabase,
) -> None:
    assert await database.migration_readiness() == {
        "ready": True,
        "status": "compatible",
        "expected_revision": "20260727_0001",
        "deployed_revision": "20260727_0001",
    }
    async with database.engine.connect() as connection:
        tables = set(
            await connection.run_sync(
                lambda sync: set(Base.metadata.tables) | set(sync.dialect.get_table_names(sync))
            )
        )
    assert tables >= EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES - {"alembic_version"}


async def test_readiness_rejects_missing_behind_and_diverged_revision(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as session:
        await session.execute(text("DROP TABLE alembic_version"))
    assert (await database.migration_readiness())["status"] == "version_table_missing"
    async with database.transaction() as session:
        await session.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        )
        await session.execute(text("INSERT INTO alembic_version VALUES ('20260726_9999')"))
    assert (await database.migration_readiness())["status"] == "behind_expected_head"
    async with database.transaction() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='diverged_revision'"))
    assert (await database.migration_readiness())["status"] == "ahead_or_diverged"
    async with database.transaction() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='20260727_0001'"))


async def test_explicit_downgrade_and_upgrade_from_empty_database(
    database: PostgresDatabase,
) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    await asyncio.to_thread(command.downgrade, config, "base")
    async with database.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(sync.dialect.get_table_names(sync)))
    assert not (EXPECTED_TABLES - {"alembic_version"}) & names
    await asyncio.to_thread(command.upgrade, config, "head")
    assert (await database.migration_readiness())["ready"] is True


async def test_atomic_state_and_outbox_commit_and_rollback(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(database)
    workflow = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "workflow_id": "workflow-atomic",
        "state": "Running",
        "version": 1,
        "correlation_id": "correlation-1",
        "payload": b"{}",
    }
    async with database.transaction() as session:
        await session.execute(insert(WorkflowRow).values(**workflow))
        await store.record_in_transaction(session, event("event-atomic"))
    async with database.transaction() as session:
        assert await session.get(WorkflowRow, ("tenant-1", "workspace-1", "workflow-atomic"))
        assert await session.get(OutboxEventRow, ("tenant-1", "workspace-1", "event-atomic"))
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await session.execute(
                insert(WorkflowRow).values(**(workflow | {"workflow_id": "workflow-rollback"}))
            )
            await store.record_in_transaction(session, event("event-rollback"))
            raise RuntimeError("rollback")
    async with database.transaction() as session:
        assert (
            await session.get(WorkflowRow, ("tenant-1", "workspace-1", "workflow-rollback")) is None
        )
        assert (
            await session.get(OutboxEventRow, ("tenant-1", "workspace-1", "event-rollback")) is None
        )


async def test_claim_locking_ack_failure_visibility_and_stale_reclamation(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(database)
    for index in range(4):
        await store.record(event(f"event-{index}"))
    claimed: list[set[str]] = []

    async def claim(owner: str) -> None:
        rows = await store.claim(owner=owner, batch_size=2, lease_seconds=30)
        claimed.append({item.event_id for item in rows})

    async with anyio.create_task_group() as group:
        group.start_soon(claim, "worker-a")
        group.start_soon(claim, "worker-b")
    assert claimed[0].isdisjoint(claimed[1])
    assert len(claimed[0] | claimed[1]) == 4
    first = next(iter(claimed[0]))
    assert await store.mark_delivered(
        first,
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        owner="worker-a",
    )
    second = next(iter(claimed[1]))
    assert await store.release_failed(
        second,
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        owner="worker-b",
        error="poison event",
        backoff_seconds=1,
    )
    later = datetime.now(UTC) + timedelta(seconds=31)
    reclaimed = await store.claim(owner="worker-c", batch_size=4, lease_seconds=30, now=later)
    assert first not in {item.event_id for item in reclaimed}
    async with database.transaction() as session:
        poison = await session.get(OutboxEventRow, ("tenant-1", "workspace-1", second))
        assert poison is not None and poison.last_error == "poison event"


async def test_cross_scope_constraints_and_repository_isolation(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as session:
        await session.execute(
            insert(WorkflowRow).values(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                workflow_id="shared-id",
                state="Running",
                version=1,
                correlation_id="correlation-a",
                payload=b"{}",
            )
        )
        await session.execute(
            insert(WorkflowRow).values(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                workflow_id="shared-id",
                state="Running",
                version=1,
                correlation_id="correlation-b",
                payload=b"{}",
            )
        )
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(
                insert(WorkflowStepRow).values(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    workflow_step_id="step-cross",
                    workflow_id="missing-in-scope",
                    state="Running",
                    attempt_number=0,
                    payload=b"{}",
                )
            )
    async with database.transaction() as session:
        rows = tuple(
            await session.scalars(
                select(WorkflowRow).where(
                    WorkflowRow.tenant_id == "tenant-a",
                    WorkflowRow.workspace_id == "workspace-a",
                )
            )
        )
    assert len(rows) == 1 and rows[0].correlation_id == "correlation-a"


async def test_postgres_composed_runtime_restart_idempotency_lineage_and_evidence(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    first = compose(settings)
    command_envelope = first.reference_runtime.build_request_command(
        "durable hello", command_id="command-durable", max_attempts=2
    )
    first_result = await first.reference_runtime.run_command(command_envelope)
    assert first_result.result_status is ResultStatus.SUCCEEDED
    workflow_id = first_result.subject_reference
    await first.close()

    restarted = compose(settings)
    second_result = await restarted.reference_runtime.run_command(command_envelope)
    assert second_result == first_result
    assert workflow_id in restarted.reference_runtime.workflow_repository.instances
    assert restarted.reference_runtime.decisions.contains(command_envelope.causation_id)
    async with restarted.database.transaction() as session:  # type: ignore[union-attr]
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)) == 3
        executions = tuple(
            await session.scalars(select(ExecutionRow).order_by(ExecutionRow.attempt_number))
        )
        assert len(executions) == 1
        assert executions[0].previous_execution_id is None
        assert (await session.scalar(select(func.count()).select_from(OutcomeRow)) or 0) >= 3
        assert (
            await session.scalar(select(func.count()).select_from(DecisionEvidenceRow)) or 0
        ) >= 1
        assert not tuple(
            await session.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type.like("%Command%"))
            )
        )
    await restarted.close()


async def test_concurrent_duplicate_submission_creates_one_workflow(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    left = compose(settings)
    right = compose(settings)
    command_envelope = left.reference_runtime.build_request_command(
        "concurrent hello", command_id="command-concurrent"
    )
    results: list[ResultEnvelope] = []

    async def run(root: CompositionRoot) -> None:
        result = await root.reference_runtime.run_command(command_envelope)
        results.append(result)

    async with anyio.create_task_group() as group:
        group.start_soon(run, left)
        group.start_soon(run, right)
    assert results[0] == results[1]
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    await left.close()
    await right.close()


async def test_retry_creates_new_execution_and_durable_decision_lineage(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        mock_ai_failures_before_success=1,
    )
    root = compose(settings)
    result = await root.reference_runtime.run("retry hello", max_attempts=2)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        executions = tuple(
            await session.scalars(select(ExecutionRow).order_by(ExecutionRow.attempt_number))
        )
        assert len(executions) == 2
        assert executions[0].execution_id != executions[1].execution_id
        assert executions[1].previous_execution_id == executions[0].execution_id
        decisions = tuple(await session.scalars(select(DecisionEvidenceRow)))
        assert any(item.decision_type == "RetryExecutionAttempt" for item in decisions)
        terminal = tuple(
            await session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type.in_(("WorkflowCompleted", "WorkflowFailed"))
                )
            )
        )
        assert len(terminal) == 1
    await root.close()


async def test_incomplete_publication_and_idempotency_resume_after_restart(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        delivery_backoff_seconds=0.01,
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    consumers = cast(
        dict[str, list[tuple[str, Any]]],
        vars(runtime.event_bus)["_consumers"],
    )
    original = consumers["ExecutionAttemptSucceeded"][0][1]

    class FailOnce:
        failed = False

        async def consume(self, delivered: EventEnvelope) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected process interruption")
            await original.consume(delivered)

    consumers["ExecutionAttemptSucceeded"] = [("workflow-engine", FailOnce())]
    command_envelope = runtime.build_request_command("resume hello", command_id="command-resume")
    acknowledgement = await runtime.run_command(command_envelope)
    assert acknowledgement.result_status is ResultStatus.ACCEPTED
    await interrupted.close()

    await anyio.sleep(0.02)
    recovered = compose(settings)
    result = await recovered.reference_runtime.run_command(command_envelope)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        terminal = tuple(
            await session.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type == "WorkflowCompleted")
            )
        )
        assert len(terminal) == 1
    await recovered.close()
