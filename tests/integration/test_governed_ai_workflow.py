"""Composed Phase 6 reference-workflow proof (offline deterministic provider)."""

from datetime import UTC, datetime

import pytest

from aieos.contracts import ResultStatus
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos_api.composition import compose
from aieos_api.settings import HostSettings


def runtime():
    return compose(
        HostSettings(),
        clock=DeterministicClock(datetime(2026, 8, 25, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("statement", "route"),
    [
        ("Where is the report?", "question_queue"),
        ("Send the report", "instruction_queue"),
        ("The report is ready.", "information_queue"),
    ],
)
async def test_classify_and_route_task_has_one_deterministic_terminal_route(
    statement: str, route: str
) -> None:
    root = runtime()
    accepted = await root.reference_runtime.classify_and_route_task(statement)
    instance = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    assert accepted.result_status is ResultStatus.ACCEPTED
    assert instance.outcome is not None and instance.outcome.value_reference == route
    assert instance.outcome.result_status is ResultStatus.SUCCEEDED
    invocation = next(iter(root.reference_runtime.reference_ai_gateway.store.invocations.values()))
    admission = invocation.request.workflow_ai_budget_admission
    assert admission is not None
    assert invocation.request.idempotency_key == admission["GatewayIdempotencyKey"]
    assert admission["CommandId"] == invocation.request.command_id
    assert admission["ExecutionId"] == invocation.request.execution_id


@pytest.mark.anyio
async def test_invalid_reference_input_rejects_before_gateway():
    root = runtime()
    result = await root.reference_runtime.classify_and_route_task(" ")
    assert result.result_status is ResultStatus.REJECTED
    assert not root.reference_runtime.reference_ai_gateway.store.invocations


@pytest.mark.anyio
async def test_exhausted_budget_rejects_before_gateway():
    root = runtime()
    result = await root.reference_runtime.classify_and_route_task(
        "What is this?", budget_ceiling="0.000001"
    )
    assert result.result_status is ResultStatus.REJECTED
    assert result.metadata == {}
    assert not root.reference_runtime.reference_ai_gateway.store.invocations
