"""Composed ES-016 execution through Skill Runtime and ReferenceAIGateway."""

import asyncio
import csv
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.contracts import ResultStatus
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.skill_runtime import TaskKind, evaluate_predictions
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos_api.composition import CompositionRoot, compose


def command(
    root: CompositionRoot,
    *,
    command_id: str = "structured-command",
    execution_id: str = "execution-structured",
    statement: str = "What is the status?",
) -> CommandEnvelope:
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
        execution_id=execution_id,
        payload={
            "skill_version_id": "structured-task-kind-skill-v1",
            "statement": statement,
        },
        metadata=CommandMetadata(
            request_id="request-structured",
            attempt_number=1,
            idempotency_key=execution_id,
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
async def test_cancelled_gateway_result_uses_governed_failed_event_and_keeps_correlation() -> None:
    root = compose()
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    adapter = runtime.reference_ai_gateway._adapters[  # pyright: ignore[reportPrivateUsage]
        "mock-economy"
    ]
    assert isinstance(adapter, DeterministicMockProvider)
    adapter._behaviors = [MockProviderBehavior.CANCELLED]  # pyright: ignore[reportPrivateUsage]

    await runtime.skill_runtime.handle(command(root))

    record = runtime.execution_repository.records["execution-structured"]
    assert record.result is not None and record.result.result_status is ResultStatus.CANCELLED
    assert record.terminal_event is not None
    assert record.terminal_event.event_type == "ExecutionAttemptFailed"
    assert isinstance(record.result.metadata["ai_invocation_id"], str)


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


@pytest.mark.anyio
async def test_v2_authoritative_result_reuse_is_durable_runtime_owned_and_zero_call() -> None:
    root = compose()
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    await runtime.skill_runtime.handle(command(root))
    source = runtime.execution_repository.records["execution-structured"].result
    assert source is not None
    reused = CommandEnvelope(
        command_id="structured-command-reuse",
        command_type="DispatchExecutionAttempt",
        command_version="2",
        correlation_id="structured-correlation-reuse",
        causation_id="workflow-command-reuse",
        target_component="Skill Runtime",
        initiator="Workflow Engine",
        timestamp=datetime(2026, 8, 15, tzinfo=UTC),
        tenant_id=root.settings.tenant_id,
        workspace_id=root.settings.workspace_id,
        workflow_id="workflow-structured-reuse",
        workflow_step_id="step-structured-reuse",
        execution_id="execution-structured-reuse",
        payload={"statement": "What is the status?"},
        metadata=CommandMetadata(
            request_id="request-structured-reuse",
            attempt_number=1,
            idempotency_key="execution-structured-reuse",
            authorization=runtime.authorization,
            skill_version_id="structured-task-kind-skill-v1",
            authoritative_result_id=source.result_id,
        ),
    )

    await runtime.skill_runtime.handle(reused)

    result = runtime.execution_repository.records["execution-structured-reuse"].result
    assert result is not None and result.result_status is ResultStatus.SUCCEEDED
    assert result.result_id != source.result_id
    assert result.value_reference == source.value_reference
    assert result.metadata["reused_result_id"] == source.result_id
    assert result.metadata["ai_invocation_id"] == ""
    assert result.metadata["avoided_model_calls"] == 1
    assert len(runtime.reference_ai_gateway.store.invocations) == 1


@pytest.mark.anyio
async def test_protected_evaluation_uses_real_composed_release_path() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "packages/skill_runtime/tests/fixtures/structured_task_kind_protected_v1.csv"
    )
    with fixture.open(encoding="utf-8", newline="") as source:
        rows = tuple(csv.DictReader(source))
    root = compose()
    runtime = root.reference_runtime
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    actual: list[TaskKind] = []
    for index, row in enumerate(rows):
        execution_id = f"protected-execution-{index}"
        await runtime.skill_runtime.handle(
            command(
                root,
                command_id=f"protected-command-{index}",
                execution_id=execution_id,
                statement=row["statement"],
            )
        )
        result = runtime.execution_repository.records[execution_id].result
        assert result is not None and result.value_reference is not None
        actual.append(TaskKind(json.loads(result.value_reference)["task_kind"]))

    expected = tuple(TaskKind(row["task_kind"]) for row in rows)
    evidence = evaluate_predictions(expected, tuple(actual))
    assert len(rows) == 100
    assert evidence.passed
    assert evidence.accuracy >= Decimal("0.95")
    assert set(evidence.per_class_recall) == set(TaskKind)
    assert all(value >= Decimal("0.90") for value in evidence.per_class_recall.values())
    assert len(runtime.reference_ai_gateway.store.invocations) == 100
