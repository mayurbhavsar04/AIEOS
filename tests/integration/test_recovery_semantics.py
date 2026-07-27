"""Focused recovery and causation regressions for PR #15."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from aieos.contracts import ResultStatus
from aieos.contracts.commands import CommandEnvelope
from aieos.contracts.events import EventEnvelope
from aieos.workflow_engine import WorkflowState
from aieos_api.composition import compose
from aieos_api.settings import HostSettings


class FailOnceDispatcher:
    """Fail one matching dispatch before delegating."""

    def __init__(
        self,
        delegate: Any,
        predicate: Callable[[CommandEnvelope], bool],
    ) -> None:
        self._delegate = delegate
        self._predicate = predicate
        self.failed = False

    async def dispatch(self, command: CommandEnvelope) -> Any:
        if not self.failed and self._predicate(command):
            self.failed = True
            raise RuntimeError("injected dispatch failure")
        return await self._delegate.dispatch(command)


class FailOnceOutbox:
    """Record an Event, then fail its first matching drain."""

    def __init__(self, delegate: Any, event_type: str) -> None:
        self._delegate = delegate
        self._event_type = event_type
        self._pending_match = False
        self.failed = False

    def record(self, event: EventEnvelope) -> None:
        self._delegate.record(event)
        self._pending_match = event.event_type == self._event_type

    async def drain(self) -> int:
        if self._pending_match and not self.failed:
            self.failed = True
            self._pending_match = False
            raise RuntimeError("injected publication failure")
        self._pending_match = False
        return await self._delegate.drain()


@pytest.mark.anyio
async def test_retry_event_redelivery_resumes_failed_dispatch_once() -> None:
    root = compose(HostSettings(mock_ai_failures_before_success=1))
    runtime = root.reference_runtime
    failing = FailOnceDispatcher(
        runtime.dispatcher,
        lambda command: (
            command.command_type == "DispatchExecutionAttempt"
            and command.metadata.attempt_number == 2
        ),
    )
    cast(Any, runtime.workflow_engine)._dispatcher = failing

    with pytest.raises(RuntimeError, match="injected dispatch failure"):
        await runtime.run("resume retry", max_attempts=2)

    instance = next(iter(runtime.workflow_repository.instances.values()))
    failed_event = next(
        event
        for event in runtime.event_bus.published
        if event.event_type == "ExecutionAttemptFailed"
    )
    assert failed_event.event_id not in instance.processed_event_ids
    assert len(instance.execution_ids) == 2

    await runtime.workflow_engine.consume(failed_event)
    await runtime.workflow_engine.consume(failed_event)

    assert instance.state is WorkflowState.COMPLETED
    assert failed_event.event_id in instance.processed_event_ids
    assert len(instance.execution_ids) == 2
    assert len(runtime.execution_repository.records) == 2


@pytest.mark.anyio
async def test_terminal_event_redelivery_completes_missing_transition_once() -> None:
    root = compose()
    runtime = root.reference_runtime
    original = cast(Any, runtime.workflow_engine)._publish_workflow_event
    failed = False

    async def fail_after_terminal_publish(
        instance: Any, event_type: str, causation_id: str
    ) -> None:
        nonlocal failed
        await original(instance, event_type, causation_id)
        if event_type == "WorkflowCompleted" and not failed:
            failed = True
            raise RuntimeError("injected terminal transition failure")

    cast(Any, runtime.workflow_engine)._publish_workflow_event = fail_after_terminal_publish

    with pytest.raises(RuntimeError, match="injected terminal transition failure"):
        await runtime.run("resume terminal")

    succeeded_event = next(
        event
        for event in runtime.event_bus.published
        if event.event_type == "ExecutionAttemptSucceeded"
    )
    instance = next(iter(runtime.workflow_repository.instances.values()))
    assert succeeded_event.event_id not in instance.processed_event_ids

    await runtime.workflow_engine.consume(succeeded_event)
    await runtime.workflow_engine.consume(succeeded_event)

    assert instance.state is WorkflowState.COMPLETED
    assert succeeded_event.event_id in instance.processed_event_ids
    assert (
        sum(event.event_type == "WorkflowCompleted" for event in runtime.event_bus.published) == 1
    )


@pytest.mark.anyio
async def test_in_progress_skill_command_resumes_after_start_publication_failure() -> None:
    root = compose()
    runtime = root.reference_runtime
    failing_outbox = FailOnceOutbox(runtime.outbox, "ExecutionAttemptStarted")
    cast(Any, runtime.skill_runtime)._outbox = failing_outbox
    command = runtime.build_request_command(
        "resume publication",
        command_id="command-publication",
        idempotency_key="publication",
    )

    with pytest.raises(RuntimeError, match="injected publication failure"):
        await runtime.run_command(command)

    assert len(runtime.workflow_repository.instances) == 1
    assert len(runtime.execution_repository.records) == 1
    execution_receipt = next(iter(runtime.execution_repository.command_receipts.values()))
    assert execution_receipt.completed is False

    result = await runtime.run_command(command)

    assert result.result_status is ResultStatus.SUCCEEDED
    assert len(runtime.workflow_repository.instances) == 1
    assert len(runtime.execution_repository.records) == 1
    assert execution_receipt.completed is True
    assert len(runtime.ai_gateway.invocations) == 1


@pytest.mark.anyio
async def test_manager_redelivery_reuses_workflow_after_dispatch_failure() -> None:
    root = compose()
    runtime = root.reference_runtime
    failing = FailOnceDispatcher(
        runtime.dispatcher,
        lambda command: command.command_type == "DispatchExecutionAttempt",
    )
    cast(Any, runtime.workflow_engine)._dispatcher = failing
    command = runtime.build_request_command(
        "resume workflow dispatch",
        command_id="command-workflow-dispatch",
        idempotency_key="workflow-dispatch",
    )

    with pytest.raises(RuntimeError, match="injected dispatch failure"):
        await runtime.run_command(command)

    instance = next(iter(runtime.workflow_repository.instances.values()))
    first_execution_id = instance.execution_ids[0]
    result = await runtime.run_command(command)

    assert result.result_status is ResultStatus.SUCCEEDED
    assert len(runtime.workflow_repository.instances) == 1
    assert instance.execution_ids == (first_execution_id,)


@pytest.mark.anyio
async def test_completed_and_scoped_idempotency_remain_authoritative() -> None:
    root = compose()
    runtime = root.reference_runtime
    command = runtime.build_request_command(
        "completed receipt",
        command_id="command-completed",
        idempotency_key="completed",
    )

    first = await runtime.run_command(command)
    second = await runtime.run_command(command)

    assert first == second
    with pytest.raises(ValueError, match="scope must match"):
        command.__class__(
            command_id=command.command_id,
            command_type=command.command_type,
            command_version=command.command_version,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            target_component=command.target_component,
            initiator=command.initiator,
            timestamp=command.timestamp,
            tenant_id=command.tenant_id,
            workspace_id="other-workspace",
            payload=command.payload,
            metadata=command.metadata,
        )


@pytest.mark.anyio
async def test_all_decision_causation_resolves_to_recorded_evidence() -> None:
    root = compose(HostSettings(mock_ai_failures_before_success=1))
    runtime = root.reference_runtime
    request = runtime.build_request_command("trace retry", max_attempts=2)
    assert runtime.decisions.contains(request.causation_id)
    result = await runtime.run_command(request)

    assert result.result_status is ResultStatus.SUCCEEDED
    instance = next(iter(runtime.workflow_repository.instances.values()))
    assert instance.retry_commands is not None
    retry_command = next(iter(instance.retry_commands.values()))
    assert runtime.decisions.contains(retry_command.causation_id)
    retry_decision = runtime.decisions.decisions[retry_command.causation_id]
    assert retry_decision.triggering_id is not None
    assert retry_decision.correlation_id == request.correlation_id
    assert retry_decision.tenant_id == request.tenant_id
    assert retry_decision.workspace_id == request.workspace_id

    commands = {
        request.command_id,
        *runtime.workflow_repository.command_receipts,
        *runtime.execution_repository.command_receipts,
    }
    events = {event.event_id for event in runtime.event_bus.published}
    decisions = set(runtime.decisions.decisions)
    valid_causes = commands | events | decisions
    assert request.causation_id in decisions
    assert all(event.causation_id in valid_causes for event in runtime.event_bus.published)
