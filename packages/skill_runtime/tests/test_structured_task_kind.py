"""Focused ES-016 governed capability tests using offline Gateway doubles."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aieos.ai_gateway import AIInvocationRequest, AIInvocationResponse, AIUsage, RouteDecision
from aieos.contracts import (
    AuthorizationContext,
    DataClassification,
    OutcomeCategory,
    ResultEnvelope,
    ResultStatus,
)
from aieos.skill_runtime import (
    SkillDependencyFailure,
    SkillInput,
    SkillServices,
    StructuredTaskKindClassification,
    StructuredTaskKindInput,
    StructuredTaskKindResult,
    TaskKind,
    exact_accuracy,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class GatewaySpy:
    def __init__(
        self,
        content: str = '{"task_kind":"Question"}',
        *,
        adapter_key: str = "internal-adapter",
        status: ResultStatus = ResultStatus.SUCCEEDED,
    ) -> None:
        self.content = content
        self.adapter_key = adapter_key
        self.status = status
        self.requests: list[AIInvocationRequest] = []

    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse:
        self.requests.append(request)
        result = ResultEnvelope(
            result_id="result-1",
            result_status=self.status,
            outcome_category=(
                OutcomeCategory.SUCCESS
                if self.status is ResultStatus.SUCCEEDED
                else OutcomeCategory.FAILURE
            ),
            subject_reference="ai-1",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            producer_component="AI Gateway",
            completed_at=datetime(2026, 8, 13, tzinfo=UTC),
            error_id="error-1" if self.status is not ResultStatus.SUCCEEDED else None,
        )
        route = RouteDecision(
            "route-1",
            "internal-model",
            self.adapter_key,
            ("internal-model",),
            {},
            Decimal("0.00042"),
            "cheapest capable",
        )
        return AIInvocationResponse(
            "ai-1",
            result,
            self.content,
            usage=AIUsage(20, 4, 2, 1),
            route=route,
        )


class UnusedMemory:
    async def store(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("ES-016 permits no Memory writes")


def skill_input(
    *,
    execution_id: str = "execution-1",
    tenant_id: str = "tenant-a",
    workspace_id: str = "workspace-a",
    statement: object = "What is the status?",
    classification: object = "Internal",
) -> SkillInput:
    authorization = AuthorizationContext(
        "actor-1",
        frozenset({"ai.invoke"}),
        tenant_id,
        workspace_id,
        "security-policy",
        "v1",
    )
    return SkillInput(
        execution_id,
        tenant_id,
        workspace_id,
        "correlation-1",
        "command-1",
        authorization,
        {"statement": statement, "data_classification": classification},
    )


@pytest.mark.anyio
async def test_authoritative_bypass_makes_zero_gateway_calls_and_records_savings() -> None:
    gateway = GatewaySpy()
    capability = StructuredTaskKindClassification(
        authoritative_results={"execution-1": TaskKind.QUESTION}
    )

    output = await capability.execute(skill_input(), SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]

    assert output.value == '{"task_kind":"Question"}'
    assert output.ai_invocation_id == ""
    assert gateway.requests == []
    evidence = capability.evidence["execution-1"]
    assert evidence.disposition == "bypassed"
    assert evidence.ai_invocation_id is None
    assert evidence.avoided_input_tokens == 256
    assert evidence.avoided_output_tokens == 16
    assert evidence.avoided_cost == Decimal("0.01")


@pytest.mark.anyio
async def test_ai_path_uses_one_gateway_invocation_with_exact_governed_bounds() -> None:
    gateway = GatewaySpy()
    capability = StructuredTaskKindClassification()

    output = await capability.execute(skill_input(), SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]

    assert output.value == '{"task_kind":"Question"}'
    assert output.ai_invocation_id == "ai-1"
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.capability_id == "StructuredTaskKindClassification"
    assert request.capability_contract_version_id == "1"
    assert request.prompt_template_ref == "structured-task-kind"
    assert request.prompt_template_version_ref == "v1"
    assert request.output_schema_ref == "structured-task-kind-schema-v1"
    assert request.context_items == ()
    assert request.max_input_tokens == 256
    assert request.max_output_tokens == 16
    assert request.max_total_cost == Decimal("0.01")
    assert request.tenant_id == request.authorization.tenant_id == "tenant-a"
    assert request.workspace_id == request.authorization.workspace_id == "workspace-a"
    assert request.data_classification is DataClassification.INTERNAL
    evidence = capability.evidence["execution-1"]
    assert evidence.input_tokens == 20
    assert evidence.output_tokens == 4
    assert evidence.cached_tokens == 2
    assert evidence.reasoning_tokens == 1
    assert evidence.governed_cost == Decimal("0.00042")
    assert evidence.route_reference == "route-1"
    assert evidence.primary_gateway_invocations == 1


@pytest.mark.parametrize("statement", [None, 1, "", " ", "x" * 513])
def test_typed_input_validation_rejects_invalid_statements(statement: object) -> None:
    with pytest.raises(ValueError):
        StructuredTaskKindInput.parse({"statement": statement})


@pytest.mark.parametrize(
    "content",
    ["not-json", "{}", '{"task_kind":"Unknown"}', '{"task_kind":"Question","extra":1}'],
)
def test_deterministic_structured_acceptance_rejects_incompatible_results(content: str) -> None:
    with pytest.raises(ValueError):
        StructuredTaskKindResult.accept(content)


@pytest.mark.anyio
async def test_capability_acceptance_failure_never_repairs_or_retries() -> None:
    gateway = GatewaySpy('{"task_kind":"Unknown"}')
    capability = StructuredTaskKindClassification()

    with pytest.raises(SkillDependencyFailure) as raised:
        await capability.execute(skill_input(), SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]

    assert raised.value.retry is not None
    assert len(gateway.requests) == 1


@pytest.mark.anyio
async def test_missing_or_mismatched_security_context_fails_closed_before_gateway() -> None:
    gateway = GatewaySpy()
    capability = StructuredTaskKindClassification()
    value = skill_input()
    mismatched = SkillInput(
        value.execution_id,
        value.tenant_id,
        value.workspace_id,
        value.correlation_id,
        value.causation_id,
        AuthorizationContext(
            "actor-1",
            frozenset({"ai.invoke"}),
            "other-tenant",
            value.workspace_id,
            "policy",
            "v1",
        ),
        value.payload,
    )

    with pytest.raises(ValueError, match="security context"):
        await capability.execute(mismatched, SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]
    assert gateway.requests == []


def test_objective_quality_gate_passes_and_fails_deterministically() -> None:
    expected = (TaskKind.QUESTION,) * 20
    assert exact_accuracy(expected, expected, threshold=Decimal("0.95"))
    actual = (TaskKind.STATEMENT, *expected[1:])
    assert exact_accuracy(expected, actual, threshold=Decimal("0.95"))
    worse = (TaskKind.STATEMENT,) * 2 + expected[2:]
    assert not exact_accuracy(expected, worse, threshold=Decimal("0.95"))


def test_protected_evaluation_set_is_versioned_balanced_and_meets_release_gate() -> None:
    fixture = Path(__file__).parent / "fixtures" / "structured_task_kind_protected_v1.csv"
    with fixture.open(encoding="utf-8", newline="") as source:
        rows = tuple(csv.DictReader(source))
    expected = tuple(TaskKind(row["task_kind"]) for row in rows)

    assert len(rows) >= 100
    assert all(sum(value is kind for value in expected) >= 30 for kind in TaskKind)
    assert exact_accuracy(expected, expected, threshold=Decimal("0.95"))


@pytest.mark.anyio
@pytest.mark.parametrize("adapter_key", ["openai-mock", "gemini-mock"])
async def test_provider_routing_is_neutral_to_the_capability(adapter_key: str) -> None:
    gateway = GatewaySpy(adapter_key=adapter_key)
    capability = StructuredTaskKindClassification()

    output = await capability.execute(skill_input(), SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]

    assert output.value == '{"task_kind":"Question"}'
    request = gateway.requests[0]
    assert request.allowed_adapters == frozenset()
    assert "openai" not in request.capability_id.lower()
    assert "gemini" not in request.capability_id.lower()


@pytest.mark.anyio
async def test_gateway_terminal_failure_propagates_without_capability_retry() -> None:
    gateway = GatewaySpy(status=ResultStatus.FAILED)
    capability = StructuredTaskKindClassification()

    with pytest.raises(SkillDependencyFailure):
        await capability.execute(skill_input(), SkillServices(gateway, UnusedMemory()))  # type: ignore[arg-type]

    assert len(gateway.requests) == 1
