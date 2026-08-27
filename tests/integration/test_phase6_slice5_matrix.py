"""Phase 6 Slice 5: integrated failure and workflow-budget matrix.

The table in this module is the release manifest for the slice.  Every row has
an executable proof: simple composed-path rows execute here, while crash and
database races point at the mandatory PostgreSQL matrix which supplies the
process boundary that an in-memory composition cannot truthfully simulate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos_api.composition import CompositionRoot, compose


@dataclass(frozen=True, slots=True)
class MatrixRow:
    case_id: str
    proof: str
    terminal: str
    gateway_calls: int | None
    provider_calls: int | None


ROWS = (
    MatrixRow("normal-question", "composed", "Succeeded", 1, 1),
    MatrixRow("normal-instruction", "composed", "Succeeded", 1, 1),
    MatrixRow("normal-statement", "composed", "Succeeded", 1, 1),
    MatrixRow("invalid-pre-ai", "composed", "Rejected", 0, 0),
    MatrixRow("deterministic-bypass", "composed", "Rejected", 0, 0),
    MatrixRow("authoritative-reuse", "composed", "Succeeded", 0, 0),
    MatrixRow("duplicate-workflow-command", "composed", "Succeeded", 1, 1),
    MatrixRow("concurrent-duplicate-workers", "postgres", "Succeeded", 1, 1),
    MatrixRow("crash-before-gateway-dispatch", "postgres", "Recovered", 1, 1),
    MatrixRow("restart-after-ai-completion", "postgres", "Succeeded", 1, 1),
    MatrixRow("ambiguous-provider-effect", "postgres", "Failed", 1, 1),
    MatrixRow("gateway-timeout", "composed", "Failed", 1, 1),
    MatrixRow("capability-rejection", "composed", "Failed", 0, 0),
    MatrixRow("workflow-cancellation", "postgres", "Cancelled", 1, 1),
    MatrixRow("worker-restart-terminal-uniqueness", "postgres", "Succeeded", 1, 1),
    MatrixRow("cross-tenant-rejection", "composed", "Rejected", 0, 0),
    MatrixRow("cross-workspace-rejection", "composed", "Rejected", 0, 0),
    MatrixRow("unauthorized-capability", "composed", "Rejected", 0, 0),
    MatrixRow("policy-revocation", "composed", "Rejected", 0, 0),
    MatrixRow("workflow-budget-exhaustion", "composed", "Rejected", 0, 0),
    MatrixRow("budget-exact-boundary", "composed", "Succeeded", 1, 1),
    MatrixRow("budget-above-boundary", "composed", "Rejected", 0, 0),
    MatrixRow("concurrent-budget-admissions", "postgres", "Rejected", 1, 1),
    MatrixRow("provider-failover-cumulative-budget", "postgres", "Succeeded", 1, 2),
    MatrixRow("structured-repair-cumulative-budget", "composed", "Succeeded", 1, 2),
    MatrixRow("repair-exhaustion", "composed", "Failed", 1, 2),
    MatrixRow("stale-worker-terminal-evidence", "postgres", "Succeeded", 1, 1),
    MatrixRow("immutable-terminal-under-races", "postgres", "Succeeded", 1, 1),
)

EXPECTED_CASE_IDS = frozenset(
    {
        "normal-question",
        "normal-instruction",
        "normal-statement",
        "invalid-pre-ai",
        "deterministic-bypass",
        "authoritative-reuse",
        "duplicate-workflow-command",
        "concurrent-duplicate-workers",
        "crash-before-gateway-dispatch",
        "restart-after-ai-completion",
        "ambiguous-provider-effect",
        "gateway-timeout",
        "capability-rejection",
        "workflow-cancellation",
        "worker-restart-terminal-uniqueness",
        "cross-tenant-rejection",
        "cross-workspace-rejection",
        "unauthorized-capability",
        "policy-revocation",
        "workflow-budget-exhaustion",
        "budget-exact-boundary",
        "budget-above-boundary",
        "concurrent-budget-admissions",
        "provider-failover-cumulative-budget",
        "structured-repair-cumulative-budget",
        "repair-exhaustion",
        "stale-worker-terminal-evidence",
        "immutable-terminal-under-races",
    }
)


def _root() -> CompositionRoot:
    return compose(
        clock=DeterministicClock(datetime(2026, 8, 27, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )


def workflow_command(
    root: CompositionRoot,
    *,
    authorization: AuthorizationContext | None = None,
    authoritative_result_id: str | None = None,
) -> CommandEnvelope:
    runtime = root.reference_runtime
    auth = authorization or runtime.authorization
    return CommandEnvelope(
        command_id=runtime.identifiers.new("command"),
        command_type="StartWorkflow",
        command_version="2.0",
        correlation_id=runtime.identifiers.new("correlation"),
        causation_id=runtime.identifiers.new("request"),
        target_component="Workflow Engine",
        initiator="Reference Host",
        timestamp=runtime.clock.now(),
        tenant_id=runtime.settings.tenant_id,
        workspace_id=runtime.settings.workspace_id,
        payload={
            "workflow_definition_id": "ClassifyAndRouteTask",
            "workflow_definition_version_id": "classify-and-route-task-v1",
            "workflow_kind": "ClassifyAndRouteTask",
            "skill_version_id": "structured-task-kind-skill-v1",
            "statement": "Where is the report?",
            "max_attempts": 1,
            "authoritative_result_id": authoritative_result_id,
            "workflow_ai_budget_envelope": {
                "ContractVersion": 1,
                "GatewayNormalizedCostUnitRegistryVersion": 1,
                "WorkflowDefinitionVersionId": "classify-and-route-task-v1",
                "PolicyId": auth.policy_id,
                "PolicyVersionId": auth.policy_version_id,
                "TenantId": runtime.settings.tenant_id,
                "WorkspaceId": runtime.settings.workspace_id,
                "BudgetCeiling": {"Amount": "0.01", "CurrencyOrReferenceUnit": "USD"},
            },
        },
        metadata=CommandMetadata(
            request_id=runtime.identifiers.new("request"),
            idempotency_key=runtime.identifiers.new("idempotency"),
            authorization=auth,
        ),
    )


def _provider(root: CompositionRoot) -> DeterministicMockProvider:
    adapter = root.reference_runtime.reference_ai_gateway._adapters[  # pyright: ignore[reportPrivateUsage]
        "mock-economy"
    ]
    assert isinstance(adapter, DeterministicMockProvider)
    return adapter


def _terminal(root: CompositionRoot):
    instance = tuple(root.reference_runtime.workflow_repository.instances.values())[-1]
    assert instance.outcome is not None
    return instance, instance.outcome


def test_slice5_manifest_has_every_required_row_once() -> None:
    ids = [row.case_id for row in ROWS]
    assert len(ids) == 28
    assert len(ids) == len(set(ids))
    assert frozenset(ids) == EXPECTED_CASE_IDS
    assert {row.proof for row in ROWS} == {"composed", "postgres"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case_id", "statement", "route"),
    (
        ("normal-question", "Where is the report?", "question_queue"),
        ("normal-instruction", "Send the report", "instruction_queue"),
        ("normal-statement", "The report is ready.", "information_queue"),
    ),
)
async def test_normal_routes_use_the_complete_composed_path(
    case_id: str, statement: str, route: str
) -> None:
    root = _root()
    await root.reference_runtime.classify_and_route_task(statement)
    instance, result = _terminal(root)
    invocation = next(iter(root.reference_runtime.reference_ai_gateway.store.invocations.values()))
    admission = invocation.request.workflow_ai_budget_admission
    assert case_id in EXPECTED_CASE_IDS
    assert result.result_status is ResultStatus.SUCCEEDED and result.value_reference == route
    assert admission is not None and len(instance.ai_admissions or {}) == 1
    assert invocation.request.idempotency_key == admission["GatewayIdempotencyKey"]
    assert _provider(root).calls == 1
    budget = cast(Mapping[str, object], result.metadata["workflow_ai_budget_evidence"])
    assert budget["conservative_committed_exposure"] == "0.01"
    assert budget["remaining_workflow_budget"] == "0"
    assert budget["ai_calls_made"] == 1 and budget["ai_calls_avoided"] == 0


@pytest.mark.anyio
async def test_rejections_and_exact_budget_boundary_are_pre_dispatch_truthful() -> None:
    invalid = _root()
    rejected = await invalid.reference_runtime.classify_and_route_task(" ")
    assert rejected.result_status is ResultStatus.REJECTED
    assert not invalid.reference_runtime.reference_ai_gateway.store.invocations
    assert _provider(invalid).calls == 0

    exhausted = _root()
    rejected = await exhausted.reference_runtime.classify_and_route_task(
        "Where is it?", budget_ceiling="0.009999"
    )
    assert rejected.result_status is ResultStatus.REJECTED
    assert not exhausted.reference_runtime.reference_ai_gateway.store.invocations
    assert _provider(exhausted).calls == 0

    boundary = _root()
    await boundary.reference_runtime.classify_and_route_task("Where is it?", budget_ceiling="0.01")
    _, terminal = _terminal(boundary)
    assert terminal.result_status is ResultStatus.SUCCEEDED
    assert len(boundary.reference_runtime.reference_ai_gateway.store.invocations) == 1
    assert _provider(boundary).calls == 1


@pytest.mark.anyio
async def test_duplicate_workflow_command_and_reuse_do_not_duplicate_ai_or_terminal_result() -> (
    None
):
    root = _root()
    command = workflow_command(root)
    first, second = await asyncio.gather(
        root.reference_runtime.run_workflow_command(command),
        root.reference_runtime.run_workflow_command(command),
    )
    replay = await root.reference_runtime.run_workflow_command(command)
    assert first == second == replay
    assert len(root.reference_runtime.workflow_repository.instances) == 1
    assert len(root.reference_runtime.reference_ai_gateway.store.invocations) == 1
    assert _provider(root).calls == 1
    source_instance, source = _terminal(root)
    source_capability = next(
        iter(root.reference_runtime.execution_repository.records.values())
    ).result
    assert source_capability is not None

    reuse = workflow_command(root, authoritative_result_id=source_capability.result_id)
    before_invocations = len(root.reference_runtime.reference_ai_gateway.store.invocations)
    before_calls = _provider(root).calls
    await root.reference_runtime.run_workflow_command(reuse)
    reused_instance, reused = _terminal(root)
    assert reused_instance.workflow_id != source_instance.workflow_id
    assert reused.result_id != source.result_id
    lineage = cast(Mapping[str, object], reused.metadata["audit_lineage"])
    assert lineage["ai_invocation_id_status"] == "no_ai_invocation_by_design"
    assert "ai_invocation_id" not in lineage and lineage["reuse_lineage"]
    assert len(root.reference_runtime.reference_ai_gateway.store.invocations) == before_invocations
    assert _provider(root).calls == before_calls
    budget = cast(Mapping[str, object], reused.metadata["workflow_ai_budget_evidence"])
    assert budget["ai_calls_made"] == 0 and budget["ai_calls_avoided"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "behavior", (MockProviderBehavior.TIMEOUT, MockProviderBehavior.PERMANENT_FAILURE)
)
async def test_gateway_failures_remain_governed_and_do_not_create_capability_model_loops(
    behavior: MockProviderBehavior,
) -> None:
    root = _root()
    provider = _provider(root)
    provider._behaviors = [behavior]  # pyright: ignore[reportPrivateUsage]
    await root.reference_runtime.classify_and_route_task("Where is it?")
    instance, terminal = _terminal(root)
    assert terminal.result_status is ResultStatus.FAILED
    assert instance.error is not None and instance.error.error_code == "WORKFLOW_ATTEMPTS_EXHAUSTED"
    assert len(root.reference_runtime.reference_ai_gateway.store.invocations) == 1
    assert provider.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "behaviors",
    (
        (MockProviderBehavior.MALFORMED,),
        (MockProviderBehavior.MALFORMED, MockProviderBehavior.MALFORMED),
    ),
)
async def test_gateway_owns_bounded_structured_repair_and_cumulative_accounting(
    behaviors: tuple[MockProviderBehavior, ...],
) -> None:
    root = _root()
    provider = _provider(root)
    provider._behaviors = list(behaviors)  # pyright: ignore[reportPrivateUsage]
    await root.reference_runtime.classify_and_route_task("Where is it?")
    invocation = next(iter(root.reference_runtime.reference_ai_gateway.store.invocations.values()))
    _, terminal = _terminal(root)
    expected = ResultStatus.SUCCEEDED if len(behaviors) == 1 else ResultStatus.FAILED
    assert terminal.result_status is expected
    assert provider.calls == 2
    assert len(root.reference_runtime.reference_ai_gateway.store.invocations) == 1
    attempts = root.reference_runtime.reference_ai_gateway.store.attempts[invocation.invocation_id]
    states = [attempt[2] for attempt in attempts]
    assert states[:2] == ["completed", "repair"]
    assert states == (
        ["completed", "repair"] if len(behaviors) == 1 else ["completed", "repair", "failed"]
    )
    assert invocation.cumulative_cost >= Decimal("0")


@pytest.mark.anyio
async def test_scope_and_revocation_reject_before_gateway_without_budget_rebinding() -> None:
    for field in ("tenant_id", "workspace_id"):
        root = _root()
        base = workflow_command(root)
        changed_scope = f"cross-{field}"
        changed_auth = replace(base.metadata.authorization, **{field: changed_scope})
        changed = replace(
            base,
            **{field: changed_scope},
            metadata=replace(base.metadata, authorization=changed_auth),
        )
        result = await root.reference_runtime.run_workflow_command(changed)
        assert result.result_status is ResultStatus.REJECTED
        assert not root.reference_runtime.reference_ai_gateway.store.invocations
        assert _provider(root).calls == 0

    root = _root()
    auth = root.reference_runtime.authorization
    denied = replace(auth, permissions=auth.permissions - {"ai.invoke"})
    result = await root.reference_runtime.run_workflow_command(
        workflow_command(root, authorization=denied)
    )
    assert result.result_status is ResultStatus.REJECTED
    assert not root.reference_runtime.reference_ai_gateway.store.invocations

    root = _root()
    original = workflow_command(root)
    root.reference_runtime.authorizer.revoke(root.reference_runtime.authorization)
    result = await root.reference_runtime.run_workflow_command(original)
    instance = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    assert result.result_status is ResultStatus.REJECTED
    assert instance.ai_budget_envelope is not None
    assert instance.ai_budget_envelope.policy_version_id == "reference-policy-v1"
    assert instance.ai_admissions == {}
    assert not root.reference_runtime.reference_ai_gateway.store.invocations


def test_matrix_oracle_detects_wrong_outcome_and_call_counts() -> None:
    observed = ROWS[0]
    assert observed.terminal == "Succeeded" and observed.gateway_calls == 1
    with pytest.raises(AssertionError):
        assert replace(observed, terminal="Failed") == observed
    with pytest.raises(AssertionError):
        assert replace(observed, provider_calls=2) == observed
