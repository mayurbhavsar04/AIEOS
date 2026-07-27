"""Integration coverage for the executable AIEOS reference workflow."""

from datetime import UTC, datetime

import pytest

from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.memory_service import MemoryWrite
from aieos.skill_runtime import ExecutionState
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos.workflow_engine import WorkflowState
from aieos_api.composition import CompositionRoot, compose
from aieos_api.settings import HostSettings


def runtime(
    *,
    failures_before_success: int = 0,
    delay_seconds: float = 0.0,
    timeout_seconds: float = 1.0,
) -> CompositionRoot:
    return compose(
        HostSettings(
            mock_ai_failures_before_success=failures_before_success,
            mock_ai_delay_seconds=delay_seconds,
            reference_timeout_seconds=timeout_seconds,
        ),
        clock=DeterministicClock(datetime(2026, 7, 23, 8, 0, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )


@pytest.mark.anyio
async def test_reference_workflow_succeeds_end_to_end() -> None:
    root = runtime()
    result = await root.reference_runtime.run("prove the runtime")

    assert result.result_status is ResultStatus.SUCCEEDED
    assert result.value_reference == "Hello from AIEOS: prove the runtime"
    instance = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    assert instance.state is WorkflowState.COMPLETED
    assert instance.attempt_number == 1
    assert len(root.reference_runtime.memory_repository.records) == 1
    assert {
        "WorkflowStarted",
        "ExecutionAttemptStarted",
        "ExecutionAttemptSucceeded",
        "WorkflowCompleted",
    } <= {event.event_type for event in root.reference_runtime.event_bus.published}


@pytest.mark.anyio
async def test_workflow_engine_creates_new_execution_id_for_retry() -> None:
    root = runtime(failures_before_success=1)
    result = await root.reference_runtime.run("retry once", max_attempts=2)

    assert result.result_status is ResultStatus.SUCCEEDED
    instance = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    assert instance.attempt_number == 2
    assert len(instance.execution_ids) == 2
    assert len(set(instance.execution_ids)) == 2
    first, second = (
        root.reference_runtime.execution_repository.records[execution_id]
        for execution_id in instance.execution_ids
    )
    assert first.state is ExecutionState.FAILED
    assert second.state is ExecutionState.SUCCEEDED


@pytest.mark.anyio
async def test_target_owned_idempotency_returns_same_result_without_duplicate_workflow() -> None:
    root = runtime()
    command = root.reference_runtime.build_request_command(
        "idempotent execution",
        command_id="command-fixed",
        idempotency_key="fixed-key",
    )

    first = await root.reference_runtime.run_command(command)
    second = await root.reference_runtime.run_command(command)

    assert first == second
    assert len(root.reference_runtime.workflow_repository.instances) == 1
    assert len(root.reference_runtime.execution_repository.records) == 1


@pytest.mark.anyio
async def test_manager_normalizes_invalid_request_payload() -> None:
    root = runtime()
    command = root.reference_runtime.build_request_command("valid")
    invalid = command.__class__(
        command_id=command.command_id,
        command_type=command.command_type,
        command_version=command.command_version,
        correlation_id=command.correlation_id,
        causation_id=command.causation_id,
        target_component=command.target_component,
        initiator=command.initiator,
        timestamp=command.timestamp,
        tenant_id=command.tenant_id,
        workspace_id=command.workspace_id,
        payload={"message": ""},
        metadata=command.metadata,
    )

    result = await root.reference_runtime.run_command(invalid)

    assert result.result_status is ResultStatus.REJECTED
    assert result.error_id is not None
    assert not root.reference_runtime.workflow_repository.instances


def test_memory_service_denies_cross_workspace_access() -> None:
    root = runtime()
    memory = root.reference_runtime.memory_service.store(
        MemoryWrite(
            content="scoped",
            tenant_id=root.settings.tenant_id,
            workspace_id=root.settings.workspace_id,
            correlation_id="correlation-memory",
            provenance="test",
            authorization=root.reference_runtime.authorization,
        )
    )
    other_scope = AuthorizationContext(
        actor_id="other",
        permissions=frozenset({"memory.read"}),
        tenant_id=root.settings.tenant_id,
        workspace_id="other-workspace",
        policy_id="policy",
        policy_version_id="policy-v1",
    )

    with pytest.raises(PermissionError):
        root.reference_runtime.memory_service.fetch(
            memory.memory_id,
            tenant_id=root.settings.tenant_id,
            workspace_id="other-workspace",
            authorization=other_scope,
        )


@pytest.mark.anyio
async def test_timeout_is_terminal_for_attempt_and_normalized_for_workflow() -> None:
    root = runtime(delay_seconds=0.05, timeout_seconds=0.001)
    result = await root.reference_runtime.run("time out", max_attempts=1)

    assert result.result_status is ResultStatus.FAILED
    attempt = next(iter(root.reference_runtime.execution_repository.records.values()))
    assert attempt.state is ExecutionState.TIMED_OUT
    assert attempt.result is not None
    assert attempt.result.result_status is ResultStatus.TIMED_OUT
    assert "ExecutionAttemptTimedOut" in {
        event.event_type for event in root.reference_runtime.event_bus.published
    }


@pytest.mark.anyio
async def test_observability_records_are_identified_scoped_and_correlated() -> None:
    root = runtime()
    await root.reference_runtime.run("observe")

    records = root.reference_runtime.observations.records
    assert records
    assert len({record.log_record_id for record in records}) == len(records)
    assert all(record.context.tenant_id == root.settings.tenant_id for record in records)
    assert all(record.context.workspace_id == root.settings.workspace_id for record in records)
    assert all(record.context.correlation_id for record in records)
    assert all(record.context.data_classification.value == "NonSensitive" for record in records)
    assert all(record.context.redaction_status.value == "NotRequired" for record in records)
