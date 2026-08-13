"""Composed ES-016 execution through Skill Runtime and ReferenceAIGateway."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider
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
    adapter = runtime.reference_ai_gateway._adapters["mock-economy"]  # pyright: ignore[reportPrivateUsage]
    assert isinstance(adapter, DeterministicMockProvider)
    assert adapter.calls == 1
    assert len(adapter.prompts) == 1
    assembled = adapter.prompts[0]
    assert "<task class='classification'>\nWhat is the status?\n</task>" in assembled
    assert "<schema ref='structured-task-kind-schema-v1'>" in assembled
    assert "history" not in assembled.lower()
    assert "<evidence" not in assembled
    capability_record = next(
        record
        for record in runtime.observations.records
        if record.attributes.get("capability_id") == "StructuredTaskKindClassification"
        and record.context.component_identity == "Skill Runtime"
    )
    assert capability_record.context.ai_invocation_id == invocation.invocation_id
    assert capability_record.attributes["accounting_correlation"] == "ai_invocation_id"
    assert capability_record.attributes["provider_attempt_count_status"] == "canonical_store"


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


@pytest.mark.anyio
async def test_composed_exact_business_payload_rejects_extra_fields_before_gateway() -> None:
    root = compose()
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    delivery = command(root)
    delivery = replace(delivery, payload={**delivery.payload, "data_classification": "Internal"})

    await runtime.skill_runtime.handle(delivery)

    record = runtime.execution_repository.records["execution-structured"]
    assert record.result is not None and record.result.result_status is ResultStatus.FAILED
    assert runtime.reference_ai_gateway.store.invocations == {}
