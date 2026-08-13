"""Composed ES-016 execution through Skill Runtime and ReferenceAIGateway."""

import asyncio
from datetime import UTC, datetime

import pytest

from aieos.contracts import ResultStatus
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos_api.composition import CompositionRoot, compose


def command(root: CompositionRoot, *, command_id: str = "structured-command") -> CommandEnvelope:
    runtime = root.reference_runtime
    return CommandEnvelope(
        command_id=command_id,
        command_type="DispatchExecutionAttempt",
        command_version="1.0",
        correlation_id="structured-correlation",
        causation_id="workflow-command",
        target_component="Skill Runtime",
        initiator="Workflow Engine",
        timestamp=datetime(2026, 8, 13, tzinfo=UTC),
        tenant_id=root.settings.tenant_id,
        workspace_id=root.settings.workspace_id,
        workflow_id="workflow-structured",
        workflow_step_id="step-structured",
        execution_id="execution-structured",
        payload={
            "skill_version_id": "structured-task-kind-skill-v1",
            "statement": "What is the status?",
        },
        metadata=CommandMetadata(
            request_id="request-structured",
            attempt_number=1,
            idempotency_key="execution-structured",
            authorization=runtime.authorization,
        ),
    )


@pytest.mark.anyio
async def test_composed_capability_resolves_schema_and_uses_real_gateway() -> None:
    root = compose(
        clock=DeterministicClock(datetime(2026, 8, 13, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]

    result = await runtime.skill_runtime.handle(command(root))

    assert result.result_status is ResultStatus.ACCEPTED
    record = runtime.execution_repository.records["execution-structured"]
    assert record.result is not None and record.result.result_status is ResultStatus.SUCCEEDED
    assert record.result.value_reference == '{"task_kind":"Question"}'
    invocation = next(iter(runtime.reference_ai_gateway.store.invocations.values()))
    assert invocation.request.output_schema_ref == "structured-task-kind-schema-v1"
    assert invocation.request.allowed_adapters == frozenset()
    assert invocation.request.context_items == ()
    assert invocation.terminal is not None


@pytest.mark.anyio
async def test_composed_replay_and_concurrent_duplicate_do_not_duplicate_ai_work() -> None:
    root = compose()
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    delivery = command(root)

    first, second = await asyncio.gather(
        runtime.skill_runtime.handle(delivery), runtime.skill_runtime.handle(delivery)
    )
    replay = await runtime.skill_runtime.handle(delivery)

    assert first == second == replay
    assert len(runtime.execution_repository.records) == 1
    assert len(runtime.reference_ai_gateway.store.invocations) == 1
