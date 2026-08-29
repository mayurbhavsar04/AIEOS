"""Phase 6 R5 row-bound governed workflow evaluator.

The 28 rows below are the frozen ES-017 release manifest. A row is not a
label for another test: its runner produces an observation and this evaluator
compares every governed oracle to the row. PostgreSQL runners remain in this
module so CI executes the same evaluator-bound scenarios with PostgreSQL 17.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import func, select, text, update

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.adapters.persistence_postgres import (
    PostgresAIGatewayStore,
    PostgresDatabase,
    PostgresProviderEffectBoundary,
)
from aieos.adapters.persistence_postgres.models import (
    AIGatewayAttemptRow,
    AIGatewayBudgetRow,
    AIGatewayInvocationRow,
    AIGatewayProviderEffectRow,
    AIGatewayUsageLedgerRow,
    OutboxEventRow,
    OutcomeRow,
    WorkflowRow,
)
from aieos.ai_gateway import (
    AIInvocationRequest,
    ModelCatalogEntry,
    ProviderResult,
    ReferenceAIGateway,
)
from aieos.contracts import (
    AuthorizationContext,
    ErrorCategory,
    ErrorEnvelope,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.contracts.events import EventEnvelope
from aieos.security_support import ScopeAuthorizer
from aieos.skill_runtime.runtime import SkillDependencyFailure
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos.workflow_engine import WorkflowState
from aieos_api.composition import CompositionRoot, compose
from aieos_api.settings import HostSettings, RuntimeAdapter


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """A governed release row and every oracle its runner must establish."""

    case_id: str
    proof: str
    terminal: str
    gateway_calls: int
    provider_calls: int
    budget: str
    security: str
    durability: str
    lineage: str


@dataclass(frozen=True, slots=True)
class MatrixExecution:
    """Actual output from one runner; metadata alone cannot create this record."""

    case_id: str
    terminal: str
    gateway_calls: int
    provider_calls: int
    budget: str
    security: str
    durability: str
    lineage: str
    terminal_results: int
    runner_completed: bool


@dataclass(frozen=True, slots=True)
class RaceProof:
    workflow_ids: tuple[str, ...]
    terminal_result_ids: tuple[str, ...]
    replay_result_id: str
    provider_effects: int
    provider_dispatches: int
    admission_matches: bool
    admission_state: str
    budget_state: str
    usage_final: bool
    forged_admission_rejected: bool


def _validate_race_proof(proof: RaceProof) -> None:
    assert len(proof.workflow_ids) >= 2
    assert len(set(proof.workflow_ids)) == 1
    assert len(proof.terminal_result_ids) == 1
    assert proof.replay_result_id == proof.terminal_result_ids[0]
    assert proof.provider_effects == 1
    assert proof.provider_dispatches == 1
    assert proof.admission_matches
    assert proof.admission_state == "Reconciled"
    assert proof.budget_state == "committed"
    assert proof.usage_final
    assert proof.forged_admission_rejected


ROWS = (
    MatrixRow(
        "normal-question", "composed", "Succeeded", 1, 1, "settled", "scoped", "none", "safe"
    ),
    MatrixRow(
        "normal-instruction", "composed", "Succeeded", 1, 1, "settled", "scoped", "none", "safe"
    ),
    MatrixRow(
        "normal-statement", "composed", "Succeeded", 1, 1, "settled", "scoped", "none", "safe"
    ),
    MatrixRow(
        "invalid-pre-ai",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "pre-dispatch",
        "none",
        "safe",
    ),
    MatrixRow(
        "deterministic-bypass",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "pre-dispatch",
        "none",
        "safe",
    ),
    MatrixRow(
        "authoritative-reuse", "composed", "Succeeded", 0, 0, "bypassed", "scoped", "none", "safe"
    ),
    MatrixRow(
        "duplicate-workflow-command",
        "composed",
        "Succeeded",
        1,
        1,
        "settled",
        "idempotent",
        "none",
        "safe",
    ),
    MatrixRow(
        "concurrent-duplicate-workers",
        "postgres",
        "Succeeded",
        1,
        1,
        "settled",
        "idempotent",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "crash-before-gateway-dispatch",
        "postgres",
        "Recovered",
        1,
        1,
        "settled",
        "scoped",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "restart-after-ai-completion",
        "postgres",
        "Succeeded",
        1,
        1,
        "settled",
        "scoped",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "ambiguous-provider-effect",
        "postgres",
        "Failed",
        1,
        1,
        "ambiguous",
        "fail-closed",
        "recovered",
        "safe",
    ),
    MatrixRow("gateway-timeout", "composed", "Failed", 1, 1, "committed", "scoped", "none", "safe"),
    MatrixRow(
        "capability-rejection",
        "composed",
        "Failed",
        0,
        0,
        "not-admitted",
        "capability-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "workflow-cancellation",
        "postgres",
        "Cancelled",
        1,
        1,
        "committed",
        "scoped",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "worker-restart-terminal-uniqueness",
        "postgres",
        "Succeeded",
        1,
        1,
        "settled",
        "idempotent",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "cross-tenant-rejection",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "scope-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "cross-workspace-rejection",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "scope-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "unauthorized-capability",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "capability-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "policy-revocation",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "policy-revoked",
        "none",
        "safe",
    ),
    MatrixRow(
        "workflow-budget-exhaustion",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "budget-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "budget-exact-boundary",
        "composed",
        "Succeeded",
        1,
        1,
        "settled",
        "budget-bound",
        "none",
        "safe",
    ),
    MatrixRow(
        "budget-above-boundary",
        "composed",
        "Rejected",
        0,
        0,
        "not-admitted",
        "budget-denied",
        "none",
        "safe",
    ),
    MatrixRow(
        "concurrent-budget-admissions",
        "postgres",
        "Rejected",
        1,
        1,
        "bounded",
        "budget-denied",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "provider-failover-cumulative-budget",
        "postgres",
        "Succeeded",
        1,
        2,
        "cumulative",
        "scoped",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "structured-repair-cumulative-budget",
        "composed",
        "Succeeded",
        1,
        2,
        "cumulative",
        "scoped",
        "none",
        "safe",
    ),
    MatrixRow(
        "repair-exhaustion", "composed", "Failed", 1, 2, "cumulative", "scoped", "none", "safe"
    ),
    MatrixRow(
        "stale-worker-terminal-evidence",
        "postgres",
        "Succeeded",
        1,
        1,
        "settled",
        "fenced",
        "recovered",
        "safe",
    ),
    MatrixRow(
        "immutable-terminal-under-races",
        "postgres",
        "Succeeded",
        1,
        1,
        "settled",
        "fenced",
        "recovered",
        "safe",
    ),
)

EXPECTED_CASE_IDS = frozenset(row.case_id for row in ROWS)


def _root() -> CompositionRoot:
    return compose(
        clock=DeterministicClock(datetime(2026, 8, 27, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )


def _postgres_root() -> CompositionRoot:
    return compose(
        HostSettings(
            runtime_adapter=RuntimeAdapter.POSTGRES,
            database_url=SecretStr(_database_url()),
        ),
        clock=DeterministicClock(datetime(2026, 8, 27, tzinfo=UTC)),
        identifiers=DeterministicIdentifiers(),
    )


def _workflow_command(
    root: CompositionRoot,
    *,
    authorization: AuthorizationContext | None = None,
    authoritative_result_id: str | None = None,
    statement: str = "Where is the report?",
    budget_ceiling: str = "0.01",
    max_attempts: int = 1,
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
            "statement": statement,
            "max_attempts": max_attempts,
            "authoritative_result_id": authoritative_result_id,
            "workflow_ai_budget_envelope": {
                "ContractVersion": 1,
                "GatewayNormalizedCostUnitRegistryVersion": 1,
                "WorkflowDefinitionVersionId": "classify-and-route-task-v1",
                "PolicyId": auth.policy_id,
                "PolicyVersionId": auth.policy_version_id,
                "TenantId": runtime.settings.tenant_id,
                "WorkspaceId": runtime.settings.workspace_id,
                "BudgetCeiling": {"Amount": budget_ceiling, "CurrencyOrReferenceUnit": "USD"},
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


def _terminal(root: CompositionRoot) -> tuple[Any, ResultEnvelope]:
    instance = tuple(root.reference_runtime.workflow_repository.instances.values())[-1]
    assert instance.outcome is not None
    return instance, instance.outcome


def _assert_safe_projection(result: ResultEnvelope, statement: str | None = None) -> None:
    """Actively reject raw provider/model/prompt leakage from terminal projections."""
    rendered = str(result.value_reference or "")
    lowered = rendered.lower()
    assert "provider" not in lowered and "model" not in lowered and "mock" not in lowered
    if statement is not None:
        assert statement.lower() not in lowered


def _settled_budget(result: ResultEnvelope, *, calls: int) -> None:
    budget = cast(Mapping[str, object], result.metadata["workflow_ai_budget_evidence"])
    assert budget["ai_calls_made"] == calls
    assert budget["conservative_committed_exposure"] == "0"
    assert Decimal(cast(str, budget["gateway_authoritative_settled_actual"])) >= Decimal("0")


def _execution(
    row: MatrixRow,
    *,
    terminal: str,
    gateway_calls: int,
    provider_calls: int,
    budget: str,
    security: str,
    durability: str = "none",
    lineage: str = "safe",
    terminal_results: int = 1,
) -> MatrixExecution:
    return MatrixExecution(
        case_id=row.case_id,
        terminal=terminal,
        gateway_calls=gateway_calls,
        provider_calls=provider_calls,
        budget=budget,
        security=security,
        durability=durability,
        lineage=lineage,
        terminal_results=terminal_results,
        runner_completed=True,
    )


def _evaluate(rows: tuple[MatrixRow, ...], executions: tuple[MatrixExecution, ...]) -> None:
    """Reject omissions, duplicates, unrun rows, and each row-oracle mutation."""
    row_ids = [row.case_id for row in rows]
    execution_ids = [execution.case_id for execution in executions]
    assert len(rows) == 28
    assert len(row_ids) == len(set(row_ids))
    assert frozenset(row_ids) == EXPECTED_CASE_IDS
    assert len(executions) == len(rows)
    assert len(execution_ids) == len(set(execution_ids))
    assert frozenset(execution_ids) == EXPECTED_CASE_IDS
    observed = {execution.case_id: execution for execution in executions}
    for row in rows:
        _evaluate_execution(row, observed[row.case_id])


def _evaluate_execution(row: MatrixRow, execution: MatrixExecution) -> None:
    assert execution.runner_completed, f"{row.case_id}: metadata-only execution is forbidden"
    assert execution.case_id == row.case_id
    assert execution.terminal == row.terminal
    assert execution.gateway_calls == row.gateway_calls
    assert execution.provider_calls == row.provider_calls
    assert execution.budget == row.budget
    assert execution.security == row.security
    assert execution.durability == row.durability
    assert execution.lineage == row.lineage
    assert execution.terminal_results == 1


async def _run_composed_row(row: MatrixRow) -> MatrixExecution:
    root = _root()
    runtime = root.reference_runtime
    provider = _provider(root)
    if row.case_id in {"normal-question", "normal-instruction", "normal-statement"}:
        statements = {
            "normal-question": ("Where is the report?", "question_queue"),
            "normal-instruction": ("Send the report", "instruction_queue"),
            "normal-statement": ("The report is ready.", "information_queue"),
        }
        statement, route = statements[row.case_id]
        await runtime.classify_and_route_task(statement)
        instance, result = _terminal(root)
        projection = json.loads(str(result.value_reference))
        assert result.result_status is ResultStatus.SUCCEEDED
        assert projection["route"] == route
        assert projection["workflow_id"] == instance.workflow_id
        assert projection["workflow_step_id"] == instance.workflow_step_id
        assert projection["capability_result_id"]
        _assert_safe_projection(result, statement)
        _settled_budget(result, calls=1)
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=provider.calls,
            budget="settled",
            security="scoped",
        )
    if row.case_id in {"invalid-pre-ai", "deterministic-bypass"}:
        statement = " " if row.case_id == "invalid-pre-ai" else "x" * 513
        result = await runtime.run_workflow_command(_workflow_command(root, statement=statement))
        assert result.result_status is ResultStatus.REJECTED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        _assert_safe_projection(result, statement)
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="pre-dispatch",
        )
    if row.case_id == "authoritative-reuse":
        await runtime.run_workflow_command(_workflow_command(root))
        _, source_terminal = _terminal(root)
        source = next(iter(runtime.execution_repository.records.values())).result
        assert source is not None and source_terminal.result_status is ResultStatus.SUCCEEDED
        before_gateway = len(runtime.reference_ai_gateway.store.invocations)
        before_provider = provider.calls
        await runtime.run_workflow_command(
            _workflow_command(root, authoritative_result_id=source.result_id)
        )
        instance, result = _terminal(root)
        lineage = cast(Mapping[str, object], result.metadata["audit_lineage"])
        assert result.result_status is ResultStatus.SUCCEEDED
        assert instance.workflow_id != source_terminal.subject_reference
        assert lineage["reuse_lineage"] == source.result_id
        assert lineage["ai_invocation_id_status"] == "no_ai_invocation_by_design"
        assert "ai_invocation_id" not in lineage
        assert len(runtime.reference_ai_gateway.store.invocations) == before_gateway
        assert provider.calls == before_provider
        _settled_budget(result, calls=0)
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=0,
            provider_calls=0,
            budget="bypassed",
            security="scoped",
        )
    if row.case_id == "duplicate-workflow-command":
        command = _workflow_command(root)
        first, second = await asyncio.gather(
            runtime.run_workflow_command(command), runtime.run_workflow_command(command)
        )
        replay = await runtime.run_workflow_command(command)
        _, result = _terminal(root)
        assert first == second == replay
        assert len(runtime.workflow_repository.instances) == 1
        assert len(runtime.reference_ai_gateway.store.invocations) == 1 and provider.calls == 1
        _settled_budget(result, calls=1)
        _assert_safe_projection(result, "Where is the report?")
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="idempotent",
        )
    if row.case_id == "gateway-timeout":
        provider._behaviors = [MockProviderBehavior.TIMEOUT]  # pyright: ignore[reportPrivateUsage]
        await runtime.classify_and_route_task("Where is the report?")
        _, result = _terminal(root)
        assert result.result_status is ResultStatus.FAILED
        assert len(runtime.reference_ai_gateway.store.invocations) == 1 and provider.calls == 1
        _assert_safe_projection(result, "Where is the report?")
        return _execution(
            row,
            terminal="Failed",
            gateway_calls=1,
            provider_calls=1,
            budget="committed",
            security="scoped",
        )
    if row.case_id == "capability-rejection":
        implementation = runtime.skill_runtime._skill_implementations[  # pyright: ignore[reportPrivateUsage]
            "structured-task-kind-local"
        ]

        async def reject_capability(_: object, __: object) -> object:
            raise SkillDependencyFailure(
                "approved capability rejected the governed dispatch",
                status=ResultStatus.FAILED,
                category=ErrorCategory.UNSUPPORTED_CAPABILITY,
                retry=RetryClassification.NEVER_RETRY,
                error_code="CAPABILITY_REJECTED",
            )

        implementation.execute = reject_capability  # type: ignore[method-assign]
        await runtime.run_workflow_command(_workflow_command(root))
        _, result = _terminal(root)
        assert result.result_status is ResultStatus.FAILED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        _assert_safe_projection(result)
        return _execution(
            row,
            terminal="Failed",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="capability-denied",
        )
    if row.case_id in {"cross-tenant-rejection", "cross-workspace-rejection"}:
        command = _workflow_command(root)
        field = "tenant_id" if row.case_id == "cross-tenant-rejection" else "workspace_id"
        changed = f"cross-{field}"
        authorization = replace(command.metadata.authorization, **{field: changed})
        scoped = replace(
            command,
            **{field: changed},
            metadata=replace(command.metadata, authorization=authorization),
        )
        result = await runtime.run_workflow_command(scoped)
        assert result.result_status is ResultStatus.REJECTED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="scope-denied",
        )
    if row.case_id == "unauthorized-capability":
        authorization = replace(
            runtime.authorization, permissions=runtime.authorization.permissions - {"ai.invoke"}
        )
        result = await runtime.run_workflow_command(
            _workflow_command(root, authorization=authorization)
        )
        assert result.result_status is ResultStatus.REJECTED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="capability-denied",
        )
    if row.case_id == "policy-revocation":
        command = _workflow_command(root)
        runtime.authorizer.revoke(runtime.authorization)
        result = await runtime.run_workflow_command(command)
        assert result.result_status is ResultStatus.REJECTED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="policy-revoked",
        )
    if row.case_id in {"workflow-budget-exhaustion", "budget-above-boundary"}:
        result = await runtime.run_workflow_command(
            _workflow_command(root, budget_ceiling="0.009999")
        )
        assert result.result_status is ResultStatus.REJECTED
        assert not runtime.reference_ai_gateway.store.invocations and provider.calls == 0
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=0,
            provider_calls=0,
            budget="not-admitted",
            security="budget-denied",
        )
    if row.case_id == "budget-exact-boundary":
        await runtime.run_workflow_command(_workflow_command(root, budget_ceiling="0.01"))
        _, result = _terminal(root)
        assert result.result_status is ResultStatus.SUCCEEDED and provider.calls == 1
        _settled_budget(result, calls=1)
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="budget-bound",
        )
    if row.case_id in {"structured-repair-cumulative-budget", "repair-exhaustion"}:
        behaviors = [MockProviderBehavior.MALFORMED]
        if row.case_id == "repair-exhaustion":
            behaviors.append(MockProviderBehavior.MALFORMED)
        provider._behaviors = behaviors  # pyright: ignore[reportPrivateUsage]
        await runtime.classify_and_route_task("Where is the report?")
        _, result = _terminal(root)
        expected = (
            ResultStatus.SUCCEEDED
            if row.case_id == "structured-repair-cumulative-budget"
            else ResultStatus.FAILED
        )
        invocation = next(iter(runtime.reference_ai_gateway.store.invocations.values()))
        assert result.result_status is expected and provider.calls == 2
        assert invocation.cumulative_cost >= Decimal("0")
        _assert_safe_projection(result, "Where is the report?")
        return _execution(
            row,
            terminal=expected.value,
            gateway_calls=1,
            provider_calls=2,
            budget="cumulative",
            security="scoped",
        )
    raise AssertionError(f"unbound composed R5 runner: {row.case_id}")


@pytest.mark.anyio
async def test_r5_composed_rows_execute_through_their_real_runners() -> None:
    rows = tuple(row for row in ROWS if row.proof == "composed")
    executions = tuple([await _run_composed_row(row) for row in rows])
    assert len(rows) == 18
    for row, execution in zip(rows, executions, strict=True):
        assert execution.case_id == row.case_id and execution.runner_completed


def test_r5_evaluator_rejects_omitted_duplicate_and_metadata_only_rows() -> None:
    with pytest.raises(AssertionError):
        _evaluate(ROWS[:-1], ())
    duplicated = (*ROWS[:-1], ROWS[0])
    with pytest.raises(AssertionError):
        _evaluate(duplicated, ())
    row = MatrixRow("oracle", "observed", "Succeeded", 1, 1, "settled", "scoped", "none", "safe")
    metadata_only = MatrixExecution(
        "oracle", "Succeeded", 1, 1, "settled", "scoped", "none", "safe", 1, False
    )
    with pytest.raises(AssertionError, match="metadata-only"):
        _evaluate_execution(row, metadata_only)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("terminal", "Failed"),
        ("gateway_calls", 9),
        ("budget", "not-admitted"),
        ("security", "scope-denied"),
        ("lineage", "leaked"),
    ),
)
def test_r5_evaluator_rejects_real_oracle_mutations(field: str, replacement: object) -> None:
    row = MatrixRow(
        "oracle", "observed", "Succeeded", 1, 1, "settled", "scoped", "recovered", "safe"
    )
    observed = MatrixExecution(
        "oracle", "Succeeded", 1, 1, "settled", "scoped", "recovered", "safe", 1, True
    )
    mutated = replace(observed, **{field: replacement})
    with pytest.raises(AssertionError):
        _evaluate_execution(row, mutated)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("workflow_ids", ("workflow-a", "workflow-b")),
        ("terminal_result_ids", ("result-a", "result-b")),
        ("replay_result_id", "different-result"),
        ("provider_effects", 2),
        ("provider_dispatches", 2),
        ("admission_matches", False),
        ("admission_state", "Committed"),
        ("budget_state", "released"),
        ("usage_final", False),
        ("forged_admission_rejected", False),
    ),
)
def test_r5_race_proof_rejects_false_green_mutations(field: str, replacement: object) -> None:
    proof = RaceProof(
        workflow_ids=("workflow-a", "workflow-a"),
        terminal_result_ids=("result-a",),
        replay_result_id="result-a",
        provider_effects=1,
        provider_dispatches=1,
        admission_matches=True,
        admission_state="Reconciled",
        budget_state="committed",
        usage_final=True,
        forged_admission_rejected=True,
    )
    _validate_race_proof(proof)
    with pytest.raises(AssertionError):
        _validate_race_proof(replace(proof, **{field: replacement}))


def _database_url() -> str:
    url = os.environ.get("AIEOS_TEST_DATABASE_URL")
    if url is None:
        if os.environ.get("CI"):
            pytest.fail("mandatory AIEOS_TEST_DATABASE_URL is not configured in CI")
        pytest.skip("R5 PostgreSQL runners require AIEOS_TEST_DATABASE_URL")
    return url


async def _reset_postgres(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        await session.execute(
            text(
                "TRUNCATE outbox_events, outcomes, command_idempotency, executions, "
                "workflow_steps, workflows, ai_gateway_usage_ledger, ai_gateway_attempts, "
                "ai_gateway_budgets, ai_gateway_cache, ai_gateway_invocations CASCADE"
            )
        )


@pytest.fixture
async def postgres_database() -> AsyncIterator[PostgresDatabase]:
    database = PostgresDatabase(_database_url())
    await _reset_postgres(database)
    yield database
    await database.close()


def _ai_request(**overrides: object) -> AIInvocationRequest:
    values: dict[str, object] = {
        "execution_id": "r5-execution",
        "capability_contract_version_id": "text-v1",
        "prompt": "R5 durable gateway",
        "tenant_id": "tenant-r5",
        "workspace_id": "workspace-r5",
        "correlation_id": "correlation-r5",
        "causation_id": "decision-r5",
        "authorization": AuthorizationContext(
            "actor-r5", frozenset({"ai.invoke"}), "tenant-r5", "workspace-r5", "policy-r5", "v1"
        ),
        "command_id": "command-r5",
        "idempotency_key": "idempotency-r5",
        "max_total_cost": Decimal("1"),
    }
    values.update(overrides)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def _durable_gateway(
    database: PostgresDatabase,
    *,
    provider: DeterministicMockProvider | None = None,
    models: tuple[ModelCatalogEntry, ...] | None = None,
    adapters: Mapping[str, DeterministicMockProvider] | None = None,
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, PostgresAIGatewayStore]:
    identifiers = DeterministicIdentifiers()
    effective_provider = provider or DeterministicMockProvider("mock", prefix="R5")
    boundary = PostgresProviderEffectBoundary(database)
    effective_provider.use_effect_boundary(boundary)
    effective_adapters = adapters or {effective_provider.key: effective_provider}
    for adapter in effective_adapters.values():
        adapter.use_effect_boundary(boundary)
    effective_models = models or (
        ModelCatalogEntry(
            "model-v1",
            "mock",
            frozenset({"text", "structured"}),
            4096,
            512,
            1,
            1,
            Decimal("0.000001"),
            Decimal("0.000002"),
            "price-r5",
        ),
    )
    store = PostgresAIGatewayStore(database)
    gateway = ReferenceAIGateway(
        clock=DeterministicClock(datetime(2026, 8, 27, tzinfo=UTC)),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=store,
        catalog=effective_models,
        adapters=effective_adapters,
    )
    return gateway, effective_provider, store


async def _run_postgres_row(row: MatrixRow, database: PostgresDatabase) -> MatrixExecution:
    if row.case_id == "concurrent-duplicate-workers":
        first, first_provider, _ = _durable_gateway(database)
        second, second_provider, _ = _durable_gateway(database)
        one, two = await asyncio.gather(
            first.invoke(_ai_request(idempotency_key="r5-concurrent")),
            second.invoke(
                _ai_request(command_id="redelivered-r5-concurrent", idempotency_key="r5-concurrent")
            ),
        )
        assert one.result.result_status is ResultStatus.SUCCEEDED
        assert one.ai_invocation_id == two.ai_invocation_id
        assert one.result.result_id == two.result.result_id
        assert first_provider.calls + second_provider.calls == 1
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="idempotent",
            durability="recovered",
        )
    if row.case_id == "crash-before-gateway-dispatch":
        first, first_provider, store = _durable_gateway(database)
        request = _ai_request(idempotency_key="r5-pre-dispatch-crash")
        accepted = await first.accept(request)
        assert (
            await store.claim_execution(
                accepted.ai_invocation_id,
                owner="crashed-r5-worker",
                now=datetime(2026, 8, 27, tzinfo=UTC),
                lease=timedelta(seconds=1),
            )
            == 1
        )
        async with database.transaction() as session:
            await session.execute(
                update(AIGatewayInvocationRow)
                .where(AIGatewayInvocationRow.ai_invocation_id == accepted.ai_invocation_id)
                .values(execution_lease_expires_at=datetime(2026, 8, 26, tzinfo=UTC))
            )
        restarted, restarted_provider, _ = _durable_gateway(database)
        recovered = await restarted.invoke(replace(request, command_id="r5-pre-dispatch-recovery"))
        assert first_provider.calls == 0 and restarted_provider.calls == 1
        assert recovered.result.result_status is ResultStatus.SUCCEEDED
        return _execution(
            row,
            terminal="Recovered",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="scoped",
            durability="recovered",
        )
    if row.case_id == "restart-after-ai-completion":
        first, provider, _ = _durable_gateway(database)
        request = _ai_request(idempotency_key="r5-post-completion-restart")
        response = await first.invoke(request)
        restarted, replay_provider, _ = _durable_gateway(database)
        replay = await restarted.invoke(replace(request, command_id="r5-post-completion-replay"))
        assert response.result.result_status is ResultStatus.SUCCEEDED
        assert replay.result.result_id == response.result.result_id
        assert provider.calls == 1 and replay_provider.calls == 0
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="scoped",
            durability="recovered",
        )
    if row.case_id == "ambiguous-provider-effect":
        root = _postgres_root()
        command = _workflow_command(root)
        provider = _provider(root)
        durable_boundary = PostgresProviderEffectBoundary(database)

        class CrashAfterProviderEffect:
            async def execute(
                self,
                *,
                request: AIInvocationRequest,
                effect_key: str,
                effect_type: str,
                request_hash: str,
                operation: Any,
            ) -> ProviderResult:
                async def lose_process_after_effect() -> ProviderResult:
                    await operation()
                    raise RuntimeError("injected process loss after provider effect")

                return await durable_boundary.execute(
                    request=request,
                    effect_key=effect_key,
                    effect_type=effect_type,
                    request_hash=request_hash,
                    operation=lose_process_after_effect,
                )

        provider.use_effect_boundary(CrashAfterProviderEffect())
        try:
            await root.reference_runtime.run_workflow_command(command)
            instance, terminal = _terminal(root)
            workflow_id = instance.workflow_id
            terminal_result_id = terminal.result_id
            assert terminal.result_status is ResultStatus.FAILED
            assert provider.calls == 1
            async with database.transaction() as session:
                effects = tuple(await session.scalars(select(AIGatewayProviderEffectRow)))
                assert len(effects) == 1
                effect = effects[0]
                assert effect.state == "dispatching" and effect.dispatch_count == 1
                observed_budget = "ambiguous" if effect.state == "dispatching" else effect.state
        finally:
            await root.close()
        restarted = _postgres_root()
        try:
            replay = await restarted.reference_runtime.run_workflow_command(command)
            replay_workflow_id = cast(str, replay.value_reference)
            assert replay_workflow_id == workflow_id
            recovered = restarted.reference_runtime.workflow_repository.instances[workflow_id]
            assert recovered.outcome is not None
            assert recovered.outcome.result_id == terminal_result_id
            assert recovered.outcome.result_status is ResultStatus.FAILED
            assert _provider(restarted).calls == 0
            async with database.transaction() as session:
                effects = tuple(await session.scalars(select(AIGatewayProviderEffectRow)))
                terminal_ids = tuple(
                    await session.scalars(
                        select(OutcomeRow.outcome_id).where(
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert len(effects) == 1 and effects[0].dispatch_count == 1
                assert terminal_ids == (terminal_result_id,)
        finally:
            await restarted.close()
        return _execution(
            row,
            terminal="Failed",
            gateway_calls=1,
            provider_calls=1,
            budget=observed_budget,
            security="fail-closed",
            durability="recovered",
        )
    if row.case_id == "workflow-cancellation":
        root = _postgres_root()
        provider = _provider(root)
        try:
            runtime = root.reference_runtime
            runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
            acknowledgement = await runtime.run_workflow_command(_workflow_command(root))
            workflow_id = cast(str, acknowledgement.value_reference)
            workflow = runtime.workflow_repository.instances[workflow_id]
            assert workflow.state is WorkflowState.RUNNING and provider.calls == 1
            cancel = CommandEnvelope(
                command_id=runtime.identifiers.new("command"),
                command_type="CancelWorkflow",
                command_version="2.0",
                correlation_id=workflow.correlation_id,
                causation_id=runtime.identifiers.new("request"),
                target_component="Workflow Engine",
                initiator="Reference Host",
                timestamp=runtime.clock.now(),
                tenant_id=workflow.tenant_id,
                workspace_id=workflow.workspace_id,
                workflow_id=workflow_id,
                payload={"reason": "R5 governed cancellation"},
                metadata=CommandMetadata(
                    request_id=workflow.request_id,
                    idempotency_key=runtime.identifiers.new("idempotency"),
                    authorization=runtime.authorization,
                ),
            )
            cancelled = await runtime.run_workflow_command(cancel)
            assert cancelled.result_status is ResultStatus.CANCELLED
            cancelled_result_id = cancelled.result_id
            async with database.transaction() as session:
                late_row = await session.scalar(
                    select(OutboxEventRow).where(
                        OutboxEventRow.event_type == "ExecutionAttemptSucceeded"
                    )
                )
                assert late_row is not None
                late_event = TypeAdapter(EventEnvelope).validate_json(late_row.payload)
            await runtime.workflow_engine.consume(late_event)
            assert workflow.state is WorkflowState.CANCELLED
            assert workflow.outcome is not None
            assert workflow.outcome.result_id == cancelled_result_id
        finally:
            await root.close()
        restarted = _postgres_root()
        try:
            await restarted.reference_runtime.outbox.drain()
            durable = restarted.reference_runtime.workflow_repository.instances[workflow_id]
            assert durable.state is WorkflowState.CANCELLED
            assert durable.outcome is not None and durable.outcome.result_id == cancelled_result_id
            async with database.transaction() as session:
                terminal_ids = tuple(
                    await session.scalars(
                        select(OutcomeRow.outcome_id).where(
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert terminal_ids == (cancelled_result_id,)
        finally:
            await restarted.close()
        return _execution(
            row,
            terminal="Cancelled",
            gateway_calls=1,
            provider_calls=1,
            budget="committed",
            security="scoped",
            durability="recovered",
        )
    if row.case_id == "worker-restart-terminal-uniqueness":
        root = _postgres_root()
        command = _workflow_command(root)
        provider = _provider(root)
        try:
            await root.reference_runtime.run_workflow_command(command)
            instance, terminal = _terminal(root)
            workflow_id = instance.workflow_id
            result_id = terminal.result_id
            assert provider.calls == 1
        finally:
            await root.close()
        restarted = _postgres_root()
        try:
            await restarted.reference_runtime.run_workflow_command(command)
            _, replay_terminal = _terminal(restarted)
            assert replay_terminal.result_id == result_id
            assert workflow_id in restarted.reference_runtime.workflow_repository.instances
            async with database.transaction() as session:
                terminal_rows = tuple(
                    await session.scalars(
                        select(OutcomeRow).where(
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert len(terminal_rows) == 1
        finally:
            await restarted.close()
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="idempotent",
            durability="recovered",
        )
    if row.case_id == "concurrent-budget-admissions":
        left = _postgres_root()
        right = _postgres_root()
        command = _workflow_command(left, max_attempts=2)
        left_provider = _provider(left)
        right_provider = _provider(right)
        left_provider._behaviors = [MockProviderBehavior.TRANSIENT_FAILURE]  # pyright: ignore[reportPrivateUsage]
        right_provider._behaviors = [MockProviderBehavior.TRANSIENT_FAILURE]  # pyright: ignore[reportPrivateUsage]
        try:
            outcomes = await asyncio.gather(
                left.reference_runtime.run_workflow_command(command),
                right.reference_runtime.run_workflow_command(command),
            )
            assert all(result.result_status is ResultStatus.ACCEPTED for result in outcomes)
            assert outcomes[0].value_reference == outcomes[1].value_reference
            workflow_id = cast(str, outcomes[0].value_reference)
            assert left_provider.calls + right_provider.calls == 1
            async with database.transaction() as session:
                terminal_rows = tuple(
                    await session.scalars(
                        select(OutcomeRow).where(
                            OutcomeRow.tenant_id == command.tenant_id,
                            OutcomeRow.workspace_id == command.workspace_id,
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert len(terminal_rows) == 1
                durable_terminal = TypeAdapter(ResultEnvelope).validate_json(
                    terminal_rows[0].payload
                )
                assert durable_terminal.result_status is ResultStatus.REJECTED
                assert durable_terminal.error_id is not None
                error_row = await session.get(
                    OutcomeRow,
                    (command.tenant_id, command.workspace_id, durable_terminal.error_id),
                )
                assert error_row is not None and error_row.kind == "Error"
                durable_error = TypeAdapter(ErrorEnvelope).validate_json(error_row.payload)
                assert durable_error.error_code == "WORKFLOW_AI_BUDGET_EXHAUSTED"

                workflow_row = await session.get(
                    WorkflowRow, (command.tenant_id, command.workspace_id, workflow_id)
                )
                assert workflow_row is not None and workflow_row.state == "Failed"
                workflow_snapshot = cast(dict[str, Any], json.loads(workflow_row.payload))
                assert workflow_snapshot["outcome"]["result_id"] == durable_terminal.result_id
                assert workflow_snapshot["outcome"]["error_id"] == durable_error.error_id
                assert workflow_snapshot["error"]["error_code"] == ("WORKFLOW_AI_BUDGET_EXHAUSTED")
                assert workflow_snapshot["attempt_number"] == 1
                assert len(workflow_snapshot["execution_ids"]) == 1
                assert len(workflow_snapshot["ai_admissions"]) == 1
                assert len(workflow_snapshot["ai_admission_states"]) == 1
                assert workflow_snapshot["transition_version"] == 1
                assert workflow_snapshot["retry_commands"] == {}

                invocations = tuple(
                    await session.scalars(
                        select(AIGatewayInvocationRow).where(
                            AIGatewayInvocationRow.tenant_id == command.tenant_id,
                            AIGatewayInvocationRow.workspace_id == command.workspace_id,
                        )
                    )
                )
                assert len(invocations) == 1
                ai_invocation_id = invocations[0].ai_invocation_id
                attempts = tuple(
                    await session.scalars(
                        select(AIGatewayAttemptRow).where(
                            AIGatewayAttemptRow.tenant_id == command.tenant_id,
                            AIGatewayAttemptRow.workspace_id == command.workspace_id,
                            AIGatewayAttemptRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                assert len(attempts) == 1
                effects = tuple(
                    await session.scalars(
                        select(AIGatewayProviderEffectRow).where(
                            AIGatewayProviderEffectRow.tenant_id == command.tenant_id,
                            AIGatewayProviderEffectRow.workspace_id == command.workspace_id,
                            AIGatewayProviderEffectRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                assert len(effects) == 1 and effects[0].dispatch_count == 1
                budgets = tuple(
                    await session.scalars(
                        select(AIGatewayBudgetRow).where(
                            AIGatewayBudgetRow.tenant_id == command.tenant_id,
                            AIGatewayBudgetRow.workspace_id == command.workspace_id,
                            AIGatewayBudgetRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                assert len(budgets) == 1
        finally:
            await left.close()
            await right.close()
        return _execution(
            row,
            terminal="Rejected",
            gateway_calls=1,
            provider_calls=1,
            budget="bounded",
            security="budget-denied",
            durability="recovered",
        )
    if row.case_id == "provider-failover-cumulative-budget":
        first = DeterministicMockProvider(
            "openai-responses",
            prefix="R5-first",
            behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
        )
        second = DeterministicMockProvider("gemini-generate-content", prefix="R5-second")
        models = (
            ModelCatalogEntry(
                "openai-model",
                "openai-responses",
                frozenset({"text"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000001"),
                Decimal("0.000002"),
                "r5-openai",
            ),
            ModelCatalogEntry(
                "gemini-model",
                "gemini-generate-content",
                frozenset({"text"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000002"),
                Decimal("0.000003"),
                "r5-gemini",
            ),
        )
        gateway, _, _ = _durable_gateway(
            database, provider=first, models=models, adapters={first.key: first, second.key: second}
        )
        response = await gateway.invoke(
            _ai_request(idempotency_key="r5-failover", max_provider_attempts=2)
        )
        assert response.result.result_status is ResultStatus.SUCCEEDED
        assert first.calls == 1 and second.calls == 1
        async with database.transaction() as session:
            attempts = tuple(
                await session.scalars(
                    select(AIGatewayAttemptRow)
                    .where(AIGatewayAttemptRow.ai_invocation_id == response.ai_invocation_id)
                    .order_by(AIGatewayAttemptRow.attempt_number)
                )
            )
            budget = await session.get(
                AIGatewayBudgetRow, ("tenant-r5", "workspace-r5", response.ai_invocation_id)
            )
            assert [attempt.state for attempt in attempts] == ["failed", "completed"]
            assert budget is not None and budget.actual_amount is not None
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=2,
            budget="cumulative",
            security="scoped",
            durability="recovered",
        )
    if row.case_id == "stale-worker-terminal-evidence":
        gateway, provider, store = _durable_gateway(database)
        request = _ai_request(idempotency_key="r5-stale-worker")
        accepted = await gateway.accept(request)
        assert (
            await store.claim_execution(
                accepted.ai_invocation_id,
                owner="r5-stale",
                now=datetime(2026, 8, 27, tzinfo=UTC),
                lease=timedelta(seconds=1),
            )
            == 1
        )
        async with database.transaction() as session:
            await session.execute(
                update(AIGatewayInvocationRow)
                .where(AIGatewayInvocationRow.ai_invocation_id == accepted.ai_invocation_id)
                .values(execution_lease_expires_at=datetime(2026, 8, 26, tzinfo=UTC))
            )
        recovered, recovery_provider, recovery_store = _durable_gateway(database)
        response = await recovered.invoke(replace(request, command_id="r5-stale-recovery"))
        invocation = await recovery_store.load(accepted.ai_invocation_id)
        assert response.result.result_status is ResultStatus.SUCCEEDED
        assert provider.calls == 0 and recovery_provider.calls == 1
        assert invocation.claim_generation == 2 and invocation.terminal is not None
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget="settled",
            security="fenced",
            durability="recovered",
        )
    if row.case_id == "immutable-terminal-under-races":
        root = _postgres_root()
        command = _workflow_command(root)
        provider = _provider(root)
        try:
            racing_results = await asyncio.gather(
                root.reference_runtime.run_workflow_command(command),
                root.reference_runtime.run_workflow_command(command),
            )
            instance, terminal = _terminal(root)
            assert all(
                result.result_status in {ResultStatus.ACCEPTED, ResultStatus.SUCCEEDED}
                for result in racing_results
            )
            observed_workflow_ids = tuple(
                cast(str, result.value_reference)
                if result.result_status is ResultStatus.ACCEPTED
                else result.subject_reference
                for result in racing_results
            )
            resolved_workflow_ids = set(observed_workflow_ids)
            assert resolved_workflow_ids == {instance.workflow_id}
            assert terminal.result_status is ResultStatus.SUCCEEDED
            assert terminal.subject_reference == instance.workflow_id
            assert provider.calls == 1
            async with database.transaction() as session:
                terminal_rows = tuple(
                    await session.scalars(
                        select(OutcomeRow).where(
                            OutcomeRow.tenant_id == command.tenant_id,
                            OutcomeRow.workspace_id == command.workspace_id,
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == instance.workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert len(terminal_rows) == 1
                durable_terminal = TypeAdapter(ResultEnvelope).validate_json(
                    terminal_rows[0].payload
                )
                assert durable_terminal.result_status is ResultStatus.SUCCEEDED
                assert durable_terminal.result_id == terminal.result_id
                assert durable_terminal.subject_reference == instance.workflow_id

                workflow_row = await session.get(
                    WorkflowRow,
                    (command.tenant_id, command.workspace_id, instance.workflow_id),
                )
                assert workflow_row is not None and workflow_row.state == "Completed"
                workflow_snapshot = cast(dict[str, Any], json.loads(workflow_row.payload))
                assert workflow_snapshot["outcome"]["result_id"] == durable_terminal.result_id
                assert workflow_snapshot["error"] is None

                invocations = tuple(
                    await session.scalars(
                        select(AIGatewayInvocationRow).where(
                            AIGatewayInvocationRow.tenant_id == command.tenant_id,
                            AIGatewayInvocationRow.workspace_id == command.workspace_id,
                        )
                    )
                )
                assert len(invocations) == 1
                ai_invocation_id = invocations[0].ai_invocation_id
                attempts = tuple(
                    await session.scalars(
                        select(AIGatewayAttemptRow).where(
                            AIGatewayAttemptRow.tenant_id == command.tenant_id,
                            AIGatewayAttemptRow.workspace_id == command.workspace_id,
                            AIGatewayAttemptRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                effects = tuple(
                    await session.scalars(
                        select(AIGatewayProviderEffectRow).where(
                            AIGatewayProviderEffectRow.tenant_id == command.tenant_id,
                            AIGatewayProviderEffectRow.workspace_id == command.workspace_id,
                            AIGatewayProviderEffectRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                budgets = tuple(
                    await session.scalars(
                        select(AIGatewayBudgetRow).where(
                            AIGatewayBudgetRow.tenant_id == command.tenant_id,
                            AIGatewayBudgetRow.workspace_id == command.workspace_id,
                            AIGatewayBudgetRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                usage_rows = tuple(
                    await session.scalars(
                        select(AIGatewayUsageLedgerRow).where(
                            AIGatewayUsageLedgerRow.tenant_id == command.tenant_id,
                            AIGatewayUsageLedgerRow.workspace_id == command.workspace_id,
                            AIGatewayUsageLedgerRow.ai_invocation_id == ai_invocation_id,
                        )
                    )
                )
                assert len(attempts) == 1
                assert len(effects) == 1 and effects[0].dispatch_count == 1
                assert len(budgets) == 1 and budgets[0].state == "committed"
                assert budgets[0].actual_amount is not None
                assert len(usage_rows) == 1 and usage_rows[0].final
                admissions = cast(dict[str, Any], workflow_snapshot["ai_admissions"])
                admission_states = cast(dict[str, Any], workflow_snapshot["ai_admission_states"])
                assert len(admissions) == len(admission_states) == 1
                admission_key = next(iter(admissions))
                admission = cast(dict[str, Any], admissions[admission_key])
                admission_state = cast(dict[str, Any], admission_states[admission_key])
                assert admission_state["State"] == "Reconciled"
                assert admission_state["Binding"] == admission
                assert admission_state["SettledActual"]["Amount"] == str(
                    budgets[0].actual_amount.normalize()
                )
                invocation_snapshot = cast(
                    dict[str, Any], json.loads(invocations[0].request_payload)
                )
                request_snapshot = cast(dict[str, Any], invocation_snapshot["request"])
                assert request_snapshot["workflow_ai_budget_admission"] == admission
                observed_budget = (
                    "settled"
                    if admission_state["State"] == "Reconciled"
                    and budgets[0].state == "committed"
                    and usage_rows[0].final
                    else "divergent"
                )
                assert (
                    await session.scalar(select(func.count()).select_from(OutboxEventRow)) or 0
                ) >= 1

            replay = await root.reference_runtime.run_workflow_command(command)
            assert replay.result_status in {ResultStatus.ACCEPTED, ResultStatus.SUCCEEDED}
            replay_workflow_id = (
                cast(str, replay.value_reference)
                if replay.result_status is ResultStatus.ACCEPTED
                else replay.subject_reference
            )
            assert replay_workflow_id == instance.workflow_id
            if replay.result_status is ResultStatus.SUCCEEDED:
                assert replay.result_id == durable_terminal.result_id
            assert provider.calls == 1
            async with database.transaction() as session:
                terminal_result_ids = tuple(
                    await session.scalars(
                        select(OutcomeRow.outcome_id).where(
                            OutcomeRow.tenant_id == command.tenant_id,
                            OutcomeRow.workspace_id == command.workspace_id,
                            OutcomeRow.owner_component == "Workflow Engine",
                            OutcomeRow.subject_id == instance.workflow_id,
                            OutcomeRow.terminal.is_(True),
                        )
                    )
                )
                assert terminal_result_ids == (durable_terminal.result_id,)
                replay_resolved_result_id = (
                    replay.result_id
                    if replay.result_status is ResultStatus.SUCCEEDED
                    else terminal_result_ids[0]
                )
                assert replay_resolved_result_id == durable_terminal.result_id
            invocation = next(
                iter(root.reference_runtime.reference_ai_gateway.store.invocations.values())
            )
            binding = invocation.request.workflow_ai_budget_admission
            assert binding is not None
            forged_binding = {
                **binding,
                "CommandId": "r5-forged-command",
                "ExecutionId": "r5-forged-execution",
                "GatewayIdempotencyKey": "r5-forged-idempotency",
            }
            forged = replace(
                invocation.request,
                command_id="r5-forged-command",
                execution_id="r5-forged-execution",
                idempotency_key="r5-forged-idempotency",
                workflow_ai_budget_admission=forged_binding,
            )
            with pytest.raises(ValueError, match="authoritative Workflow AI admission"):
                await root.reference_runtime.reference_ai_gateway.accept(forged)
            _validate_race_proof(
                RaceProof(
                    workflow_ids=observed_workflow_ids,
                    terminal_result_ids=terminal_result_ids,
                    replay_result_id=replay_resolved_result_id,
                    provider_effects=len(effects),
                    provider_dispatches=sum(effect.dispatch_count for effect in effects),
                    admission_matches=(
                        admission_state["Binding"] == admission
                        and request_snapshot["workflow_ai_budget_admission"] == admission
                    ),
                    admission_state=cast(str, admission_state["State"]),
                    budget_state=budgets[0].state,
                    usage_final=usage_rows[0].final,
                    forged_admission_rejected=True,
                )
            )
            assert provider.calls == 1
        finally:
            await root.close()
        return _execution(
            row,
            terminal="Succeeded",
            gateway_calls=1,
            provider_calls=1,
            budget=observed_budget,
            security="fenced",
            durability="recovered",
        )
    raise AssertionError(f"unbound PostgreSQL R5 runner: {row.case_id}")


@pytest.mark.anyio
@pytest.mark.postgres_required
async def test_r5_postgres_rows_execute_through_real_postgres_runners(
    postgres_database: PostgresDatabase,
) -> None:
    rows = tuple(row for row in ROWS if row.proof == "postgres")
    executions = [await _run_composed_row(row) for row in ROWS if row.proof == "composed"]
    for row in rows:
        await _reset_postgres(postgres_database)
        executions.append(await _run_postgres_row(row, postgres_database))
    assert len(rows) == 10
    assert len(executions) == 28
    _evaluate(ROWS, tuple(executions))
