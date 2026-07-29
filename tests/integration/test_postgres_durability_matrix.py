"""Mandatory real-PostgreSQL durability and recovery matrix."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError

from aieos.adapters.event_bus_in_process import InProcessEventBus
from aieos.adapters.persistence_postgres import (
    PostgresDatabase,
    PostgresOutboxRelay,
    PostgresOutboxStore,
    PostgresWorkflowRepository,
)
from aieos.adapters.persistence_postgres.models import (
    Base,
    CommandIdempotencyRow,
    DecisionEvidenceRow,
    DeliveryReceiptRow,
    ExecutionRow,
    MemoryRecordRow,
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
        tables = await connection.run_sync(lambda sync: set(sync.dialect.get_table_names(sync)))
    assert tables == EXPECTED_TABLES
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


async def test_composed_repositories_load_only_the_configured_scope(
    database: PostgresDatabase,
) -> None:
    shared_command_id = "command-shared-across-scopes"
    first_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    second_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-b",
        workspace_id="workspace-b",
    )
    first = compose(first_settings)
    second = compose(second_settings)
    first_result = await first.reference_runtime.run(
        "scope a",
        command_id=shared_command_id,
    )
    second_result = await second.reference_runtime.run(
        "scope b",
        command_id=shared_command_id,
    )
    await first.close()
    await second.close()

    first_restarted = compose(first_settings)
    second_restarted = compose(second_settings)
    first_repository = first_restarted.reference_runtime.workflow_repository
    second_repository = second_restarted.reference_runtime.workflow_repository
    assert isinstance(first_repository, PostgresWorkflowRepository)
    assert isinstance(second_repository, PostgresWorkflowRepository)
    await first_repository.prepare()
    await second_repository.prepare()
    assert set(first_repository.instances) == {first_result.subject_reference}
    assert set(second_repository.instances) == {second_result.subject_reference}
    assert first_result.subject_reference != second_result.subject_reference
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 2
    await first_restarted.close()
    await second_restarted.close()


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


async def test_scoped_idempotency_key_deduplicates_distinct_command_ids(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    left = compose(settings)
    right = compose(settings)
    first = left.reference_runtime.build_request_command(
        "same logical request",
        command_id="command-idempotency-left",
        idempotency_key="logical-request",
    )
    second = replace(
        first,
        command_id="command-idempotency-right",
        timestamp=first.timestamp + timedelta(microseconds=1),
    )
    results: list[ResultEnvelope] = []

    async def run(root: CompositionRoot, command_envelope: Any) -> None:
        results.append(await root.reference_runtime.run_command(command_envelope))

    async with anyio.create_task_group() as group:
        group.start_soon(run, left, first)
        group.start_soon(run, right, second)

    assert results[0] == results[1]
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        manager_receipts = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager"
                )
            )
        )
        assert len(manager_receipts) == 1
        assert manager_receipts[0].idempotency_key == "logical-request"
        assert manager_receipts[0].completed

    conflicting = replace(second, command_id="command-conflict", payload={"message": "changed"})
    with pytest.raises(ValueError, match="IdempotencyKey"):
        await right.reference_runtime.run_command(conflicting)
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
    await left.close()
    await right.close()


async def test_idempotency_scope_replay_rollback_and_retry_lineage_matrix(
    database: PostgresDatabase,
) -> None:
    shared_key = "matrix-logical-request"
    first_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        mock_ai_failures_before_success=1,
    )
    other_scope_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-b",
        workspace_id="workspace-b",
    )
    first = compose(first_settings)
    original = first.reference_runtime.build_request_command(
        "retry lineage",
        command_id="matrix-command-a",
        idempotency_key=shared_key,
        max_attempts=2,
    )
    result = await first.reference_runtime.run_command(original)
    assert result.result_status is ResultStatus.SUCCEEDED
    replay_command = replace(
        original,
        command_id="matrix-command-b",
        timestamp=original.timestamp + timedelta(seconds=1),
    )
    replay = await first.reference_runtime.run_command(replay_command)
    assert replay == result

    other = compose(other_scope_settings)
    independent = other.reference_runtime.build_request_command(
        "independent scope",
        command_id="matrix-command-c",
        idempotency_key=shared_key,
    )
    other_result = await other.reference_runtime.run_command(independent)
    assert other_result.subject_reference != result.subject_reference

    async with database.transaction() as session:
        workflows = tuple(
            await session.scalars(select(WorkflowRow).order_by(WorkflowRow.tenant_id))
        )
        assert len(workflows) == 2
        first_executions = tuple(
            await session.scalars(
                select(ExecutionRow)
                .where(
                    ExecutionRow.tenant_id == "tenant-a",
                    ExecutionRow.workspace_id == "workspace-a",
                )
                .order_by(ExecutionRow.attempt_number)
            )
        )
        assert len(first_executions) == 2
        assert first_executions[0].execution_id != first_executions[1].execution_id
        assert first_executions[1].previous_execution_id == first_executions[0].execution_id
        manager_claims = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager",
                    CommandIdempotencyRow.idempotency_key == shared_key,
                )
            )
        )
        assert len(manager_claims) == 2
        assert {row.tenant_id for row in manager_claims} == {"tenant-a", "tenant-b"}
        before = {
            "workflows": len(workflows),
            "executions": await session.scalar(select(func.count()).select_from(ExecutionRow)),
            "outcomes": await session.scalar(select(func.count()).select_from(OutcomeRow)),
            "events": await session.scalar(select(func.count()).select_from(OutboxEventRow)),
            "claims": await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)),
        }
    assert await first.reference_runtime.run_command(replay_command) == result
    async with database.transaction() as session:
        after = {
            "workflows": await session.scalar(select(func.count()).select_from(WorkflowRow)),
            "executions": await session.scalar(select(func.count()).select_from(ExecutionRow)),
            "outcomes": await session.scalar(select(func.count()).select_from(OutcomeRow)),
            "events": await session.scalar(select(func.count()).select_from(OutboxEventRow)),
            "claims": await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)),
        }
    assert after == before
    await first.close()
    await other.close()


async def test_idempotency_claim_and_workflow_staging_roll_back_atomically(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    participant = runtime.durable_participants[-1]
    original_flush = participant.flush_in_transaction
    injected = False

    async def fail_before_commit(session: Any) -> None:
        nonlocal injected
        await original_flush(session)
        if not injected:
            injected = True
            raise RuntimeError("injected failure before checkpoint commit")

    participant.flush_in_transaction = fail_before_commit  # type: ignore[method-assign]
    original = runtime.build_request_command(
        "rollback logical request",
        command_id="rollback-command-a",
        idempotency_key="rollback-key",
    )
    with pytest.raises(RuntimeError, match="checkpoint commit"):
        await runtime.run_command(original)
    assert injected
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 0
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
        assert await session.scalar(select(func.count()).select_from(OutcomeRow)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxEventRow)) == 0
        assert await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)) == 0
    await interrupted.close()

    recovered = compose(settings)
    retry = replace(
        original,
        command_id="rollback-command-b",
        timestamp=original.timestamp + timedelta(seconds=1),
    )
    result = await recovered.reference_runtime.run_command(retry)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CommandIdempotencyRow)
                .where(
                    CommandIdempotencyRow.target_component == "Manager",
                    CommandIdempotencyRow.completed.is_(True),
                )
            )
            == 1
        )
    await recovered.close()


async def test_idempotency_scope_columns_and_locks_are_independent(
    database: PostgresDatabase,
) -> None:
    scope_rows = (
        ("tenant-a", "workspace-a", "Manager"),
        ("tenant-b", "workspace-a", "Manager"),
        ("tenant-a", "workspace-b", "Manager"),
        ("tenant-a", "workspace-a", "Workflow Engine"),
    )
    async with database.transaction() as session:
        for tenant_id, workspace_id, target in scope_rows:
            await session.execute(
                insert(CommandIdempotencyRow).values(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    target_component=target,
                    idempotency_key="same-key",
                    command_id="same-command-id",
                    command_hash="a" * 64,
                    completed=False,
                    payload=b"scope-proof",
                )
            )
    async with database.transaction() as session:
        rows = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.idempotency_key == "same-key"
                )
            )
        )
        assert {(row.tenant_id, row.workspace_id, row.target_component) for row in rows} == set(
            scope_rows
        )

    entered: set[str] = set()
    both_entered = anyio.Event()
    release = anyio.Event()

    async def hold(name: str, scope: str) -> None:
        async with database.command_lock(scope):
            entered.add(name)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()

    async with anyio.create_task_group() as group:
        group.start_soon(hold, "tenant", "tenant-a\x1fworkspace-a\x1fManager\x1fsame-key")
        group.start_soon(hold, "workspace", "tenant-a\x1fworkspace-b\x1fManager\x1fsame-key")
        with anyio.fail_after(2):
            await both_entered.wait()
        release.set()


async def test_per_consumer_receipts_are_independent_and_recover_stale_claims(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("first", "second")},
    )
    bus = InProcessEventBus()

    class Recorder:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def consume(self, event: EventEnvelope) -> None:
            self.events.append(event.event_id)

    first = Recorder()
    second = Recorder()
    bus.subscribe("ExecutionAttemptSucceeded", "first", first)
    bus.subscribe("ExecutionAttemptSucceeded", "second", second)
    relay = PostgresOutboxRelay(
        store,
        bus,
        owner="relay-a",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    delivered_event = event("event-receipts")
    await store.record(delivered_event)
    assert await relay.drain() == 1
    assert first.events == ["event-receipts"]
    assert second.events == ["event-receipts"]
    async with database.transaction() as session:
        receipts = tuple(
            await session.scalars(
                select(DeliveryReceiptRow).order_by(DeliveryReceiptRow.consumer_name)
            )
        )
        assert [(item.consumer_name, item.status) for item in receipts] == [
            ("first", "Delivered"),
            ("second", "Delivered"),
        ]

    stale_event = event("event-stale-receipt")
    await store.record(stale_event)
    instant = datetime.now(UTC)
    assert await store.claim_receipt(
        stale_event,
        "first",
        owner="dead-worker",
        lease_seconds=1,
        now=instant,
    )
    assert not await store.claim_receipt(
        stale_event,
        "first",
        owner="competing-worker",
        lease_seconds=1,
        now=instant,
    )
    assert await store.claim_receipt(
        stale_event,
        "first",
        owner="recovery-worker",
        lease_seconds=30,
        now=instant + timedelta(seconds=2),
    )


async def test_required_consumer_snapshot_survives_missing_and_changed_registration(
    database: PostgresDatabase,
) -> None:
    stored_event = event("event-membership-snapshot")
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("first", "second")},
    )
    await store.record(stored_event)
    empty_bus = InProcessEventBus()
    empty_relay = PostgresOutboxRelay(
        store,
        empty_bus,
        owner="empty-runtime",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await empty_relay.drain() == 0
    async with database.transaction() as session:
        outbox = await session.get(
            OutboxEventRow, ("tenant-1", "workspace-1", stored_event.event_id)
        )
        assert outbox is not None
        assert outbox.required_consumer_count == 2
        assert outbox.delivered_at is None
        receipts = tuple(await session.scalars(select(DeliveryReceiptRow)))
        assert {receipt.consumer_name for receipt in receipts} == {"first", "second"}
        assert all(receipt.status == "Failed" for receipt in receipts)

    class Recorder:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def consume(self, event: EventEnvelope) -> None:
            self.events.append(event.event_id)

    restarted_bus = InProcessEventBus()
    first = Recorder()
    second = Recorder()
    optional = Recorder()
    restarted_bus.subscribe(stored_event.event_type, "first", first)
    restarted_bus.subscribe(stored_event.event_type, "second", second)
    restarted_bus.subscribe(stored_event.event_type, "later-optional", optional)
    restarted_store = PostgresOutboxStore(database, required_consumers={})
    restarted_relay = PostgresOutboxRelay(
        restarted_store,
        restarted_bus,
        owner="complete-runtime",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await restarted_relay.drain() == 1
    assert first.events == [stored_event.event_id]
    assert second.events == [stored_event.event_id]
    assert optional.events == []


async def test_receipt_contention_failure_poison_and_scope_safety_matrix(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("healthy", "poison")},
    )
    stored_event = event("event-receipt-matrix")
    await store.record(stored_event)
    instant = datetime.now(UTC)
    claims: list[bool] = []

    async def contend(owner: str) -> None:
        claims.append(
            await store.claim_receipt(
                stored_event,
                "healthy",
                owner=owner,
                lease_seconds=30,
                now=instant,
            )
        )

    async with anyio.create_task_group() as group:
        group.start_soon(contend, "worker-a")
        group.start_soon(contend, "worker-b")
    assert sorted(claims) == [False, True]
    assert await store.claim_receipt(
        stored_event,
        "poison",
        owner="worker-independent",
        lease_seconds=30,
        now=instant,
    )
    assert await store.fail_receipt(
        stored_event,
        "poison",
        owner="worker-independent",
        error="safe-poison-category",
    )
    async with database.transaction() as session:
        poison = await session.get(
            DeliveryReceiptRow,
            ("tenant-1", "workspace-1", stored_event.event_id, "poison"),
        )
        assert poison is not None
        assert poison.status == "Failed"
        assert poison.delivery_attempts == 1
        assert poison.last_error == "safe-poison-category"
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(
                insert(DeliveryReceiptRow).values(
                    tenant_id="other-tenant",
                    workspace_id="workspace-1",
                    event_id=stored_event.event_id,
                    consumer_name="cross-scope",
                    status="Pending",
                    required=True,
                    delivery_attempts=0,
                    created_at=instant,
                )
            )


async def test_crash_after_consumer_effect_has_one_durable_authoritative_effect(
    database: PostgresDatabase,
) -> None:
    stored_event = event("event-crash-after-effect")
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("durable-consumer",)},
    )
    await store.record(stored_event)

    class DurableConsumer:
        def __init__(self, *, crash: bool) -> None:
            self.crash = crash

        async def consume(self, event: EventEnvelope) -> None:
            async with database.transaction() as session:
                await session.execute(
                    postgres_insert(DecisionEvidenceRow)
                    .values(
                        tenant_id=event.tenant_id,
                        workspace_id=event.workspace_id,
                        decision_id=f"consumer-effect:{event.event_id}",
                        decision_type="ConsumerAuthoritativeEffect",
                        component="Test Durable Consumer",
                        correlation_id=event.correlation_id,
                        triggering_id=event.event_id,
                        recorded_at=event.recorded_at,
                        payload=b"authoritative-effect",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "workspace_id", "decision_id"]
                    )
                )
            if self.crash:
                raise RuntimeError("crash after effect")

    crashing_bus = InProcessEventBus()
    crashing_bus.subscribe(stored_event.event_type, "durable-consumer", DurableConsumer(crash=True))
    crashing_relay = PostgresOutboxRelay(
        store,
        crashing_bus,
        owner="crashing-worker",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await crashing_relay.drain() == 0

    recovered_bus = InProcessEventBus()
    recovered_bus.subscribe(
        stored_event.event_type,
        "durable-consumer",
        DurableConsumer(crash=False),
    )
    recovered_relay = PostgresOutboxRelay(
        PostgresOutboxStore(database),
        recovered_bus,
        owner="recovered-worker",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await recovered_relay.drain() == 1
    async with database.transaction() as session:
        effects = tuple(
            await session.scalars(
                select(DecisionEvidenceRow).where(
                    DecisionEvidenceRow.decision_type == "ConsumerAuthoritativeEffect"
                )
            )
        )
        receipt = await session.get(
            DeliveryReceiptRow,
            ("tenant-1", "workspace-1", stored_event.event_id, "durable-consumer"),
        )
        assert len(effects) == 1
        assert receipt is not None and receipt.status == "Delivered"
        assert receipt.delivery_attempts == 2


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


async def test_failed_execution_persists_error_and_terminal_result(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        mock_ai_failures_before_success=2,
    )
    root = compose(settings)
    result = await root.reference_runtime.run("fail durably", max_attempts=1)
    assert result.result_status is ResultStatus.FAILED
    assert result.error_id is not None
    async with database.transaction() as session:
        error = await session.get(
            OutcomeRow,
            (settings.tenant_id, settings.workspace_id, result.error_id),
        )
        terminal = await session.get(
            OutcomeRow,
            (settings.tenant_id, settings.workspace_id, result.result_id),
        )
        assert error is not None and error.kind == "Error"
        assert terminal is not None and terminal.kind == "Result" and terminal.terminal
    await root.close()


async def test_readiness_reports_unreachable_database_without_leaking_credentials() -> None:
    database = PostgresDatabase(
        "postgresql+asyncpg://user:secret@127.0.0.1:1/unreachable",  # pragma: allowlist secret
        pool_timeout_seconds=0.1,
        command_timeout_seconds=0.1,
    )
    try:
        status = await database.migration_readiness()
        assert status == {
            "ready": False,
            "status": "database_unreachable",
            "expected_revision": "20260727_0001",
        }
        assert "secret" not in repr(status)
    finally:
        await database.close()


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
    conflicting = replace(
        command_envelope,
        command_id="command-resume-conflict",
        payload={"message": "changed while incomplete"},
    )
    with pytest.raises(ValueError, match="IdempotencyKey"):
        await runtime.run_command(conflicting)
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    await interrupted.close()

    await anyio.sleep(0.02)
    recovered = compose(settings)
    replay_command = replace(
        command_envelope,
        command_id="command-resume-redelivery",
        timestamp=command_envelope.timestamp + timedelta(seconds=1),
    )
    result = await recovered.reference_runtime.run_command(replay_command)
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
        manager_claims = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager"
                )
            )
        )
        assert len(manager_claims) == 1
        assert manager_claims[0].command_id == command_envelope.command_id
        assert manager_claims[0].completed
    await recovered.close()


async def test_memory_terminal_checkpoint_rolls_back_and_redelivery_is_exactly_once(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    execution_repository = cast(Any, runtime.execution_repository)
    memory_repository = cast(Any, runtime.memory_repository)
    original_flush = execution_repository.flush_in_transaction
    injected = False

    async def fail_after_staged_memory(session: Any) -> None:
        nonlocal injected
        if not injected and memory_repository._pending:
            injected = True
            raise RuntimeError("injected failure after Memory staging")
        await original_flush(session)

    execution_repository.flush_in_transaction = fail_after_staged_memory
    command_envelope = runtime.build_request_command(
        "atomic memory hello",
        command_id="command-memory-atomicity",
    )

    with pytest.raises(RuntimeError, match="after Memory staging"):
        await runtime.run_command(command_envelope)
    assert injected

    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryRecordRow)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutcomeRow)
                .where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.terminal.is_(True),
                )
            )
            == 0
        )
        execution = await session.scalar(select(ExecutionRow))
        assert execution is not None and execution.state == "Executing"
        skill_receipt = await session.scalar(
            select(CommandIdempotencyRow).where(
                CommandIdempotencyRow.target_component == "Skill Runtime"
            )
        )
        assert skill_receipt is not None and not skill_receipt.completed
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.event_type.in_(
                        (
                            "ExecutionAttemptSucceeded",
                            "ExecutionAttemptFailed",
                            "ExecutionAttemptTimedOut",
                        )
                    )
                )
            )
            == 0
        )
    await interrupted.close()

    recovered = compose(settings)
    result = await recovered.reference_runtime.run_command(command_envelope)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        memories = tuple(await session.scalars(select(MemoryRecordRow)))
        assert len(memories) == 1
        execution = await session.scalar(select(ExecutionRow))
        assert execution is not None and execution.state == "Succeeded"
        terminal_outcomes = tuple(
            await session.scalars(
                select(OutcomeRow).where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.subject_id == execution.execution_id,
                    OutcomeRow.terminal.is_(True),
                )
            )
        )
        assert len(terminal_outcomes) == 1
        completed_receipts = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Skill Runtime",
                    CommandIdempotencyRow.completed.is_(True),
                )
            )
        )
        assert len(completed_receipts) == 1
        terminal_events = tuple(
            await session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "ExecutionAttemptSucceeded"
                )
            )
        )
        assert len(terminal_events) == 1
    replay = await recovered.reference_runtime.run_command(command_envelope)
    assert replay == result
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryRecordRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutcomeRow)
                .where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.terminal.is_(True),
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "ExecutionAttemptSucceeded")
            )
            == 1
        )
    await recovered.close()
