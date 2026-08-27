"""Composed Phase 6 reference-workflow proof (offline deterministic provider)."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos.workflow_engine import WorkflowState
from aieos.workflow_engine.governance import scale6
from aieos_api.composition import CompositionRoot, compose
from aieos_api.settings import HostSettings


def runtime() -> CompositionRoot:
    return compose(
        HostSettings(),
        clock=DeterministicClock(datetime(2026, 8, 25, tzinfo=UTC)),
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
    assert instance.outcome is not None and instance.outcome.value_reference is not None
    projection = json.loads(instance.outcome.value_reference)
    assert projection["task_kind"] in {"Question", "Instruction", "Statement"}
    assert projection["route"] == route
    assert projection["workflow_id"] == instance.workflow_id
    assert projection["workflow_step_id"] == instance.workflow_step_id
    assert projection["execution_id"] == instance.execution_ids[0]
    assert projection["capability_result_id"]
    assert projection["governance_evidence"] == {
        "workflow_definition_version_id": "classify-and-route-task-v1",
        "policy_id": "reference-policy",
        "policy_version_id": "reference-policy-v1",
    }
    assert not {"provider", "model", "provider_attempt"} & projection.keys()
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


@pytest.mark.anyio
async def test_success_persists_exact_scoped_ai_lineage_and_budget_evidence():
    root = runtime()
    await root.reference_runtime.classify_and_route_task("Where is the report?")
    workflow = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    assert workflow.outcome is not None
    lineage = cast(Mapping[str, object], workflow.outcome.metadata["audit_lineage"])
    assert lineage["tenant_id"] == workflow.tenant_id
    assert lineage["workspace_id"] == workflow.workspace_id
    assert lineage["workflow_id"] == workflow.workflow_id
    assert lineage["workflow_step_id"] == workflow.workflow_step_id
    assert lineage["execution_id"] == workflow.execution_ids[0]
    assert lineage["ai_invocation_id"]
    assert lineage["gateway_result_id"]
    assert lineage["capability_result_id"]
    budget = cast(Mapping[str, object], workflow.outcome.metadata["workflow_ai_budget_evidence"])
    assert workflow.ai_budget_envelope is not None
    assert budget["policy_version_id"] == workflow.ai_budget_envelope.policy_version_id
    assert budget["conservative_committed_exposure"] == "0"
    assert scale6(cast(str, budget["gateway_authoritative_settled_actual"])) > 0
    assert budget["ai_calls_made"] == 1


@pytest.mark.anyio
async def test_unauthorized_or_revoked_ai_admission_makes_zero_gateway_calls_and_keeps_snapshot():
    root = runtime()
    runtime_ = root.reference_runtime
    denied = AuthorizationContext(
        runtime_.authorization.actor_id,
        runtime_.authorization.permissions - {"ai.invoke"},
        runtime_.authorization.tenant_id,
        runtime_.authorization.workspace_id,
        runtime_.authorization.policy_id,
        runtime_.authorization.policy_version_id,
    )
    first = await runtime_.run_workflow_command(workflow_command(root, authorization=denied))
    assert first.result_status is ResultStatus.REJECTED
    assert not runtime_.reference_ai_gateway.store.invocations

    root = runtime()
    runtime_ = root.reference_runtime
    runtime_.authorizer.revoke(runtime_.authorization)
    second = await runtime_.run_workflow_command(workflow_command(root))
    workflow = next(iter(runtime_.workflow_repository.instances.values()))
    assert second.result_status is ResultStatus.REJECTED
    assert workflow.ai_budget_envelope is not None
    assert workflow.ai_budget_envelope.policy_version_id == "reference-policy-v1"
    assert workflow.ai_admissions == {}
    assert not runtime_.reference_ai_gateway.store.invocations


@pytest.mark.anyio
async def test_stale_policy_version_fails_closed_before_gateway():
    root = runtime()
    runtime_ = root.reference_runtime
    stale = AuthorizationContext(
        runtime_.authorization.actor_id,
        runtime_.authorization.permissions,
        runtime_.authorization.tenant_id,
        runtime_.authorization.workspace_id,
        runtime_.authorization.policy_id,
        "stale-policy-v0",
    )
    result = await runtime_.run_workflow_command(workflow_command(root, authorization=stale))
    assert result.result_status is ResultStatus.REJECTED
    assert not runtime_.reference_ai_gateway.store.invocations


@pytest.mark.anyio
async def test_legacy_workflow_ai_activation_is_envelope_governed_but_non_ai_remains_valid() -> (
    None
):
    root = runtime()
    ai_command = workflow_command(root)
    legacy_ai = replace(
        ai_command,
        command_version="1.0",
        payload={
            key: value
            for key, value in ai_command.payload.items()
            if key not in {"workflow_ai_budget_envelope", "workflow_kind"}
        },
    )
    rejected = await root.reference_runtime.run_workflow_command(legacy_ai)
    assert rejected.result_status is ResultStatus.REJECTED
    assert not root.reference_runtime.reference_ai_gateway.store.invocations

    non_ai = replace(
        workflow_command(root),
        command_id=root.reference_runtime.identifiers.new("command"),
        command_version="1.0",
        payload={
            "workflow_definition_id": "HelloAIEOSWorkflow",
            "workflow_definition_version_id": "hello-aieos-workflow-v1",
            "skill_version_id": "hello-aieos-skill-v1",
            "message": "hello",
            "max_attempts": 1,
        },
    )
    accepted = await root.reference_runtime.run_workflow_command(non_ai)
    assert accepted.result_status is ResultStatus.ACCEPTED
    assert accepted.value_reference is not None
    instance = root.reference_runtime.workflow_repository.instances[accepted.value_reference]
    assert instance.state is WorkflowState.COMPLETED
    assert instance.outcome is not None
    assert instance.outcome.result_status is ResultStatus.SUCCEEDED


@pytest.mark.anyio
async def test_unknown_workflow_command_version_fails_closed() -> None:
    root = runtime()
    command = replace(workflow_command(root), command_version="99.0")
    result = await root.reference_runtime.run_workflow_command(command)
    assert result.result_status is ResultStatus.REJECTED
    assert not root.reference_runtime.reference_ai_gateway.store.invocations


@pytest.mark.anyio
async def test_authoritative_reuse_has_new_result_lineage_without_ai_invocation():
    root = runtime()
    runtime_ = root.reference_runtime
    await runtime_.classify_and_route_task("Where is the report?")
    source = next(iter(runtime_.execution_repository.records.values())).result
    assert source is not None
    invocation_count = len(runtime_.reference_ai_gateway.store.invocations)

    await runtime_.run_workflow_command(
        workflow_command(root, authoritative_result_id=source.result_id)
    )
    workflows = tuple(runtime_.workflow_repository.instances.values())
    reused = workflows[-1]
    assert reused.outcome is not None
    lineage = cast(Mapping[str, object], reused.outcome.metadata["audit_lineage"])
    assert lineage["reuse_lineage"] == source.result_id
    assert lineage["ai_invocation_id_status"] == "no_ai_invocation_by_design"
    assert "ai_invocation_id" not in lineage
    assert lineage["capability_result_id"] != source.result_id
    assert len(runtime_.reference_ai_gateway.store.invocations) == invocation_count


@pytest.mark.anyio
async def test_persistable_audit_evidence_contains_no_raw_prompt_response_or_secret_fields():
    root = runtime()
    statement = "secret-looking task text must not enter audit evidence"
    await root.reference_runtime.classify_and_route_task(statement)
    workflow = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    execution = next(iter(root.reference_runtime.execution_repository.records.values()))
    assert workflow.outcome is not None and execution.result is not None
    encoded = json.dumps(
        {"workflow": workflow.outcome.metadata, "execution": execution.result.metadata},
        sort_keys=True,
    )
    assert statement not in encoded
    for forbidden in ("raw_prompt", "provider_response", "credential", "api_key", "secret"):
        assert f'"{forbidden}"' not in encoded


@pytest.mark.anyio
async def test_admission_is_durably_committed_with_fixed_logical_binding_before_gateway() -> None:
    root = runtime()
    await root.reference_runtime.classify_and_route_task("Where is the report?")
    workflow = next(iter(root.reference_runtime.workflow_repository.instances.values()))
    command = workflow.initial_attempt_command
    assert command is not None
    states = workflow.ai_admission_states or {}
    admission = states[command.command_id]
    binding = cast(Mapping[str, object], admission["Binding"])
    assert admission["State"] == "Reconciled"
    assert admission["WorkflowAdmissionStateVersion"] == workflow.transition_version == 1
    assert admission["GatewayIdempotencyKey"] == binding["GatewayIdempotencyKey"]
    assert admission["CommittedExposure"] == binding["CommittedExposure"]
    assert admission["SettledActual"]
    assert admission["LogicalAdmissionKey"] == ":".join(
        (
            workflow.tenant_id,
            workflow.workspace_id,
            workflow.workflow_id,
            workflow.workflow_step_id,
            command.command_id,
            command.execution_id or "",
        )
    )
