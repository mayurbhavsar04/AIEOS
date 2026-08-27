"""Offline conformance tests for the Milestone 6 Phase 2 gateway."""

import asyncio
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.ai_gateway import (
    AIInvocationRequest,
    ContextItem,
    InvocationState,
    ModelCatalogEntry,
    ReferenceAIGateway,
    ReferenceGatewayStore,
    ResponseMode,
)
from aieos.contracts import AuthorizationContext, DataClassification, ResultStatus
from aieos.security_support import ScopeAuthorizer
from aieos.testing import DeterministicClock, DeterministicIdentifiers


def _gateway(
    *,
    transient_failures: int = 0,
    tenant_limit: str = "100",
    economy_behaviors: tuple[MockProviderBehavior, ...] = (),
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, DeterministicMockProvider]:
    clock = DeterministicClock(datetime(2026, 8, 3, tzinfo=UTC))
    identifiers = DeterministicIdentifiers()
    economy = DeterministicMockProvider(
        "mock-economy",
        prefix="Economy",
        transient_failures=transient_failures,
        behaviors=economy_behaviors,
    )
    quality = DeterministicMockProvider("mock-quality", prefix="Quality")
    observations = InMemoryObservationRecorder(identifiers)
    gateway = ReferenceAIGateway(
        clock=clock,
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=observations,
        store=ReferenceGatewayStore(tenant_limit=Decimal(tenant_limit)),
        catalog=(
            ModelCatalogEntry(
                "economy-v1",
                "mock-economy",
                frozenset({"text", "structured", "stream"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000001"),
                Decimal("0.000002"),
                "price-v1",
            ),
            ModelCatalogEntry(
                "quality-v1",
                "mock-quality",
                frozenset({"text", "structured", "stream", "reasoning"}),
                16384,
                4096,
                3,
                2,
                Decimal("0.000004"),
                Decimal("0.000008"),
                "price-v1",
            ),
        ),
        adapters={"mock-economy": economy, "mock-quality": quality},
    )
    return gateway, economy, quality


def _request(**overrides: object) -> AIInvocationRequest:
    auth = AuthorizationContext(
        "test-user", frozenset({"ai.invoke"}), "tenant-a", "workspace-a", "policy", "v1"
    )
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "capability_contract_version_id": "text-v1",
        "prompt": "Summarize this safely",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "correlation_id": "correlation-1",
        "causation_id": "decision-1",
        "authorization": auth,
        "command_id": "command-1",
        "idempotency_key": "idem-1",
        "max_total_cost": Decimal("1"),
    }
    values.update(overrides)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def _workflow_binding() -> dict[str, object]:
    return {
        "BindingContractVersion": 1,
        "TenantId": "tenant-a",
        "WorkspaceId": "workspace-a",
        "WorkflowId": "workflow-1",
        "WorkflowStepId": "step-1",
        "CommandId": "command-1",
        "ExecutionId": "execution-1",
        "WorkflowDefinitionVersionId": "definition-v1",
        "PolicyId": "policy",
        "PolicyVersionId": "v1",
        "WorkflowAdmissionStateVersion": 1,
        "GatewayIdempotencyKey": "idem-1",
        "CommittedExposure": {"Amount": "0.01", "CurrencyOrReferenceUnit": "USD"},
        "CapabilityBinding": {
            "SkillVersionId": "skill-v1",
            "CapabilityId": "text-generation",
            "CapabilityContractVersionId": "text-v1",
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "mutated"),
    (
        ("TenantId", "tenant-b"),
        ("WorkspaceId", "workspace-b"),
        ("WorkflowId", "workflow-b"),
        ("WorkflowStepId", "step-b"),
        ("CommandId", "command-b"),
        ("ExecutionId", "execution-b"),
        ("WorkflowDefinitionVersionId", "definition-b"),
        ("PolicyId", "policy-b"),
        ("PolicyVersionId", "v2"),
        ("WorkflowAdmissionStateVersion", 0),
        ("GatewayIdempotencyKey", "idem-b"),
        ("CommittedExposure", {"Amount": "0.010000", "CurrencyOrReferenceUnit": "USD"}),
        (
            "CapabilityBinding",
            {
                "SkillVersionId": "skill-b",
                "CapabilityId": "text-generation",
                "CapabilityContractVersionId": "text-v1",
            },
        ),
    ),
)
async def test_gateway_rejects_every_mutated_workflow_admission_binding_before_provider(
    field: str, mutated: object
) -> None:
    gateway, economy, quality = _gateway()
    binding = _workflow_binding()
    binding[field] = mutated
    request = _request(
        workflow_ai_budget_admission=binding,
        workflow_id="workflow-1",
        workflow_step_id="step-1",
        workflow_definition_version_id="definition-v1",
        skill_version_id="skill-v1",
    )
    with pytest.raises(ValueError, match="Workflow AI admission"):
        await gateway.accept(request)
    assert economy.calls == quality.calls == 0


@pytest.mark.anyio
async def test_acceptance_owns_identity_and_lifecycle_order() -> None:
    gateway, _, _ = _gateway()
    request = _request()
    accepted = await gateway.accept(request)
    assert accepted.ai_invocation_id.startswith("test-ai-")
    assert accepted.acknowledgement.result_status is ResultStatus.ACCEPTED
    assert gateway.lifecycle[accepted.ai_invocation_id] == [InvocationState.REQUESTED]
    response = await gateway.execute(accepted.ai_invocation_id)
    assert response.result.result_id != accepted.acknowledgement.result_id
    assert gateway.lifecycle[accepted.ai_invocation_id] == [
        InvocationState.REQUESTED,
        InvocationState.POLICY_VALIDATED,
        InvocationState.PROVIDER_SELECTED,
        InvocationState.PREPARED,
        InvocationState.INVOKED,
        InvocationState.SUCCEEDED,
    ]


@pytest.mark.anyio
async def test_cheapest_capable_routing_and_hard_quality() -> None:
    gateway, _, _ = _gateway()
    economy = await gateway.invoke(_request())
    assert economy.route is not None and economy.route.model_key == "economy-v1"
    quality = await gateway.invoke(
        _request(
            command_id="command-2",
            idempotency_key="idem-2",
            quality_tier=3,
            latency_tier=2,
        )
    )
    assert quality.route is not None and quality.route.model_key == "quality-v1"


@pytest.mark.anyio
async def test_context_deduplication_truncation_and_boundaries() -> None:
    gateway, _, _ = _gateway(economy_behaviors=(MockProviderBehavior.MALFORMED,))
    items = (
        ContextItem("policy", "v1", "mandatory", 1, "safety", mandatory=True),
        ContextItem("dup", "v1", "first", 10, "evidence"),
        ContextItem("dup", "v1", "duplicate", 9, "duplicate"),
        ContextItem("large", "v1", "x" * 10000, 1, "optional"),
    )
    response = await gateway.invoke(_request(context_items=items, max_input_tokens=256))
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert response.usage is not None and response.usage.input_tokens < 1000


@pytest.mark.anyio
async def test_exact_cache_creates_fresh_invocation_and_result() -> None:
    gateway, economy, _ = _gateway()
    first = await gateway.invoke(_request())
    second = await gateway.invoke(_request(command_id="command-2", idempotency_key="idem-2"))
    assert economy.calls == 1
    assert second.cache_hit is True
    assert first.ai_invocation_id != second.ai_invocation_id
    assert first.result.result_id != second.result.result_id


@pytest.mark.anyio
async def test_cache_is_tenant_scoped() -> None:
    gateway, economy, _ = _gateway()
    await gateway.invoke(_request())
    auth = AuthorizationContext(
        "test-user", frozenset({"ai.invoke"}), "tenant-b", "workspace-b", "policy", "v1"
    )
    await gateway.invoke(
        _request(
            tenant_id="tenant-b",
            workspace_id="workspace-b",
            authorization=auth,
            command_id="command-2",
            idempotency_key="idem-2",
        )
    )
    assert economy.calls == 2


@pytest.mark.anyio
async def test_fallback_is_bounded_inside_one_invocation() -> None:
    gateway, economy, quality = _gateway(transient_failures=1)
    response = await gateway.invoke(_request(max_provider_attempts=2, latency_tier=2))
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert economy.calls == 1 and quality.calls == 1
    assert InvocationState.RETRYING in gateway.lifecycle[response.ai_invocation_id]


@pytest.mark.anyio
async def test_structured_output_repair_and_bounded_failure() -> None:
    gateway, economy, _ = _gateway(economy_behaviors=(MockProviderBehavior.MALFORMED,))
    repaired = await gateway.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
        )
    )
    assert repaired.result.result_status is ResultStatus.SUCCEEDED
    assert economy.calls == 2
    attempts = gateway.store.attempts[repaired.ai_invocation_id]
    assert [attempt[2] for attempt in attempts] == ["completed", "repair"]
    assert repaired.usage is not None
    initial_usage = attempts[0][3]
    assert initial_usage is not None
    assert repaired.usage.input_tokens > initial_usage.input_tokens
    gateway2, _, _ = _gateway(economy_behaviors=(MockProviderBehavior.MALFORMED,))
    failed = await gateway2.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
            repair_attempts=0,
        )
    )
    assert failed.result.result_status is ResultStatus.FAILED
    assert failed.result.error_id == failed.error.error_id if failed.error else False


@pytest.mark.anyio
async def test_stream_has_acknowledgement_deltas_and_terminal_result() -> None:
    gateway, _, _ = _gateway()
    chunks = [chunk async for chunk in gateway.stream(_request())]
    assert chunks[0].kind == "acknowledgement"
    assert chunks[1].kind == "stream_start"
    assert chunks[-1].kind == "terminal"
    assert chunks[-1].terminal is not None


@pytest.mark.anyio
async def test_admission_replay_and_payload_conflict() -> None:
    gateway, _, _ = _gateway()
    first = await gateway.accept(_request())
    replay = await gateway.accept(_request(command_id="command-2"))
    assert replay.replay is True and replay.ai_invocation_id == first.ai_invocation_id
    with pytest.raises(ValueError, match="payload conflict"):
        await gateway.accept(_request(command_id="command-3", prompt="different"))


@pytest.mark.anyio
async def test_budget_reservation_is_idempotent_and_no_double_charge() -> None:
    gateway, _, _ = _gateway()
    response = await gateway.invoke(_request())
    reservation = gateway.store.reservations[response.ai_invocation_id]
    assert reservation.state == "committed"
    before = reservation.actual
    replay = await gateway.invoke(_request(command_id="command-2"))
    assert replay.ai_invocation_id == response.ai_invocation_id
    assert gateway.store.reservations[response.ai_invocation_id].actual == before


@pytest.mark.anyio
async def test_no_network_or_raw_prompt_in_observability() -> None:
    gateway, _, _ = _gateway()
    await gateway.invoke(_request(prompt="secret-value"))
    recorder = gateway.observations
    assert isinstance(recorder, InMemoryObservationRecorder)
    assert all("secret-value" not in str(record) for record in recorder.records)


def test_public_request_contains_no_mock_execution_control() -> None:
    assert "behavior" not in {field.name for field in fields(AIInvocationRequest)}


@pytest.mark.anyio
async def test_concurrent_duplicate_admission_is_single_invocation() -> None:
    gateway, _, _ = _gateway()
    first, second = await asyncio.gather(
        gateway.accept(_request()),
        gateway.accept(_request(command_id="replay-command")),
    )
    assert first.ai_invocation_id == second.ai_invocation_id
    assert first.replay is not second.replay


@pytest.mark.anyio
async def test_fingerprint_rejects_changed_policy_and_allows_command_replay() -> None:
    gateway, _, _ = _gateway()
    accepted = await gateway.accept(_request())
    replay = await gateway.accept(_request(command_id="another-command"))
    assert replay.ai_invocation_id == accepted.ai_invocation_id
    with pytest.raises(ValueError, match="payload conflict"):
        await gateway.accept(
            _request(command_id="third-command", safety_policy_ref="different-policy")
        )


@pytest.mark.anyio
async def test_fingerprint_covers_deadline_and_authorization_policy() -> None:
    gateway, _, _ = _gateway()
    await gateway.accept(_request())
    with pytest.raises(ValueError, match="payload conflict"):
        await gateway.accept(
            _request(
                command_id="deadline-change",
                deadline=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
            )
        )
    changed_auth = AuthorizationContext(
        "test-user",
        frozenset({"ai.invoke"}),
        "tenant-a",
        "workspace-a",
        "different-policy",
        "v2",
    )
    with pytest.raises(ValueError, match="payload conflict"):
        await gateway.accept(_request(command_id="auth-change", authorization=changed_auth))


@pytest.mark.anyio
async def test_ineligible_cheaper_model_is_never_selected() -> None:
    gateway, _, _ = _gateway()
    response = await gateway.invoke(
        _request(
            quality_tier=3,
            latency_tier=2,
            required_data_handling=frozenset({"internal"}),
        )
    )
    assert response.route is not None
    assert response.route.model_key == "quality-v1"


@pytest.mark.anyio
async def test_sensitive_request_is_not_cached() -> None:
    gateway, economy, _ = _gateway()
    request = _request(data_classification=DataClassification.RESTRICTED)
    await gateway.invoke(request)
    await gateway.invoke(
        _request(
            command_id="command-2",
            idempotency_key="idem-2",
            data_classification=DataClassification.RESTRICTED,
        )
    )
    assert economy.calls == 2


@pytest.mark.anyio
async def test_no_store_cache_policy_is_enforced() -> None:
    gateway, economy, _ = _gateway()
    await gateway.invoke(_request(cache_policy_ref="no-store"))
    await gateway.invoke(
        _request(
            command_id="command-no-store",
            idempotency_key="idem-no-store",
            cache_policy_ref="no-store",
        )
    )
    assert economy.calls == 2


@pytest.mark.anyio
async def test_output_limit_and_midstream_failure_are_normalized() -> None:
    gateway, _, _ = _gateway()
    failed = await gateway.invoke(_request(max_output_tokens=1))
    assert failed.result.result_status is ResultStatus.FAILED

    streaming, _, _ = _gateway(economy_behaviors=(MockProviderBehavior.MID_STREAM_FAILURE,))
    chunks = [chunk async for chunk in streaming.stream(_request())]
    assert any(chunk.kind == "content_delta" for chunk in chunks)
    assert chunks[-1].kind == "terminal"
    assert chunks[-1].terminal is not None
    assert chunks[-1].terminal.result.result_status is ResultStatus.FAILED
    assert chunks[-1].usage is not None and chunks[-1].usage.estimated is False
    reservation = streaming.store.reservations[chunks[-1].ai_invocation_id]
    assert reservation.state == "committed" and reservation.actual is not None


@pytest.mark.anyio
async def test_streaming_gateway_deadline_is_ambiguity_safe_and_terminal_once() -> None:
    class SlowStreamProvider(DeterministicMockProvider):
        async def stream(self, **values: object):  # type: ignore[no-untyped-def,override]
            self.calls += 1
            await asyncio.sleep(0.05)
            if False:
                yield values

    gateway, _, second = _gateway()
    first = SlowStreamProvider("mock-economy", prefix="Slow")
    gateway._adapters["mock-economy"] = first  # type: ignore[attr-defined]
    chunks = [
        chunk
        async for chunk in gateway.stream(
            _request(deadline=datetime(2026, 8, 3, 0, 0, 0, 1000, tzinfo=UTC))
        )
    ]
    terminals = [chunk for chunk in chunks if chunk.kind == "terminal"]
    assert len(terminals) == 1
    assert terminals[0].terminal is not None
    assert terminals[0].terminal.error is not None
    assert terminals[0].terminal.error.error_code == "AI_PROVIDER_EFFECT_AMBIGUOUS"
    assert first.calls == 1 and second.calls == 0


@pytest.mark.anyio
async def test_failed_fallback_attempt_is_accounted_and_context_escalates_only_on_signal() -> None:
    gateway, economy, quality = _gateway(economy_behaviors=(MockProviderBehavior.LOW_CONFIDENCE,))
    context = (
        ContextItem("top", "v1", "high relevance", 10, "minimal"),
        ContextItem("later", "v1", "escalated evidence", 1, "low confidence"),
    )
    response = await gateway.invoke(
        _request(context_items=context, max_provider_attempts=2, latency_tier=2)
    )
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert "escalated evidence" not in economy.prompts[0]
    assert "escalated evidence" in quality.prompts[0]
    reservation = gateway.store.reservations[response.ai_invocation_id]
    assert response.usage is not None
    first_usage = gateway.store.attempts[response.ai_invocation_id][0][3]
    assert first_usage is not None
    successful_cost = (response.usage.input_tokens - first_usage.input_tokens) * Decimal(
        "0.000004"
    ) + (response.usage.output_tokens - first_usage.output_tokens) * Decimal("0.000008")
    assert reservation.actual is not None and reservation.actual > successful_cost
    assert [attempt[2] for attempt in gateway.store.attempts[response.ai_invocation_id]] == [
        "low_confidence",
        "completed",
    ]


@pytest.mark.anyio
async def test_route_evidence_contains_every_hard_exclusion() -> None:
    gateway, _, _ = _gateway()
    response = await gateway.invoke(
        _request(quality_tier=3, latency_tier=2, blocked_adapters=frozenset({"mock-economy"}))
    )
    assert response.route is not None
    assert response.route.model_key == "quality-v1"
    assert response.route.excluded == {"economy-v1": "quality"}


@pytest.mark.anyio
async def test_cache_identity_includes_route_constraints() -> None:
    gateway, economy, quality = _gateway()
    await gateway.invoke(_request())
    second = await gateway.invoke(
        _request(
            command_id="command-route-cache",
            idempotency_key="idem-route-cache",
            quality_tier=3,
            latency_tier=2,
        )
    )
    assert second.cache_hit is False
    assert economy.calls == 1 and quality.calls == 1


@pytest.mark.anyio
async def test_cache_invalidation_forces_fresh_provider_execution() -> None:
    gateway, economy, _ = _gateway()
    await gateway.invoke(_request())
    assert await gateway.store.invalidate_cache("tenant-a", "workspace-a") == 1
    second = await gateway.invoke(
        _request(command_id="command-invalidated", idempotency_key="idem-invalidated")
    )
    assert second.cache_hit is False
    assert economy.calls == 2


@pytest.mark.anyio
async def test_oversized_optional_context_is_deterministically_truncated() -> None:
    gateway, economy, _ = _gateway()
    oversized = "bounded-" * 1000
    response = await gateway.invoke(
        _request(
            context_items=(ContextItem("large", "v1", oversized, 10, "evidence"),),
            max_input_tokens=128,
        )
    )
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert "bounded-" in economy.prompts[0]
    assert oversized not in economy.prompts[0]
    assert response.usage is not None and response.usage.input_tokens <= 128


@pytest.mark.anyio
async def test_nested_structured_schema_is_fully_validated_and_repaired() -> None:
    gateway, _, _ = _gateway(economy_behaviors=(MockProviderBehavior.MALFORMED,))
    repaired = await gateway.invoke(
        _request(response_mode=ResponseMode.STRUCTURED, output_schema_ref="analysis-v1")
    )
    assert repaired.result.result_status is ResultStatus.SUCCEEDED
    assert repaired.content is not None and '"items": ["economy-v1"]' in repaired.content
    unresolved = await gateway.invoke(
        _request(
            command_id="command-schema",
            idempotency_key="idem-schema",
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="unknown-schema",
            repair_attempts=0,
        )
    )
    assert unresolved.result.result_status is ResultStatus.FAILED


@pytest.mark.anyio
async def test_observability_has_scoped_redacted_route_budget_cache_and_usage_evidence() -> None:
    gateway, _, _ = _gateway()
    await gateway.invoke(_request(prompt="never-log-this"))
    recorder = gateway.observations
    assert isinstance(recorder, InMemoryObservationRecorder)
    operations = {record.context.operation_name for record in recorder.records}
    assert {
        "ai.context.prepared",
        "ai.route.decided",
        "ai.budget.reserved",
        "ai.provider.attempt",
        "ai.provider.usage",
    } <= operations
    assert all("never-log-this" not in str(record) for record in recorder.records)


@pytest.mark.anyio
async def test_concurrent_invoke_has_one_execution_owner_and_provider_effect() -> None:
    gateway, economy, _ = _gateway()
    first, second = await asyncio.gather(
        gateway.invoke(_request()),
        gateway.invoke(_request(command_id="redelivered-command")),
    )
    assert economy.calls == 1
    assert first.ai_invocation_id == second.ai_invocation_id
    assert first.result.result_id == second.result.result_id


@pytest.mark.anyio
async def test_stream_rejects_over_cap_delta_before_caller_visibility() -> None:
    gateway, _, _ = _gateway()
    chunks = [chunk async for chunk in gateway.stream(_request(max_output_tokens=1))]
    assert not any(chunk.kind == "content_delta" for chunk in chunks)
    assert chunks[-1].terminal is not None
    assert chunks[-1].terminal.result.result_status is ResultStatus.FAILED


@pytest.mark.anyio
async def test_cache_identity_uses_selected_content_and_input_bound() -> None:
    gateway, economy, _ = _gateway()
    context = (ContextItem("mutable-ref", "v1", "first content", 10, "evidence"),)
    await gateway.invoke(_request(context_items=context, max_input_tokens=128))
    changed = await gateway.invoke(
        _request(
            command_id="cache-content-change",
            idempotency_key="cache-content-change",
            context_items=(ContextItem("mutable-ref", "v1", "second content", 10, "evidence"),),
            max_input_tokens=128,
        )
    )
    bound = await gateway.invoke(
        _request(
            command_id="cache-bound-change",
            idempotency_key="cache-bound-change",
            context_items=context,
            max_input_tokens=256,
        )
    )
    same = await gateway.invoke(
        _request(
            command_id="cache-canonical-same",
            idempotency_key="cache-canonical-same",
            context_items=context,
            max_input_tokens=128,
        )
    )
    assert changed.cache_hit is False
    assert bound.cache_hit is False
    assert same.cache_hit is True
    assert economy.calls == 3


@pytest.mark.anyio
async def test_cache_write_uses_final_progressive_context_and_route_identity() -> None:
    gateway, economy, quality = _gateway(
        economy_behaviors=(
            MockProviderBehavior.LOW_CONFIDENCE,
            MockProviderBehavior.SUCCESS,
            MockProviderBehavior.LOW_CONFIDENCE,
        )
    )
    context = (
        ContextItem("minimal", "v1", "minimum", 10, "initial"),
        ContextItem("expanded", "v1", "expanded evidence", 1, "escalation"),
    )
    first = await gateway.invoke(_request(context_items=context, latency_tier=2))
    assert first.route is not None and first.route.model_key == "quality-v1"

    minimal = await gateway.invoke(
        _request(
            command_id="expanded-replay",
            idempotency_key="expanded-replay",
            context_items=context,
            latency_tier=2,
        )
    )
    assert minimal.cache_hit is False
    assert minimal.route is not None and minimal.route.model_key == "economy-v1"
    assert economy.calls == 2
    assert quality.calls == 1

    gateway.store.cache = {
        key: value
        for key, value in gateway.store.cache.items()
        if value.route is not None and value.route.model_key == "quality-v1"
    }

    escalated = await gateway.invoke(
        _request(
            command_id="approved-expanded-replay",
            idempotency_key="approved-expanded-replay",
            context_items=context,
            latency_tier=2,
        )
    )
    assert escalated.cache_hit is True
    assert escalated.route is not None and escalated.route.model_key == "quality-v1"
    assert escalated.ai_invocation_id != first.ai_invocation_id
    assert escalated.result.result_id != first.result.result_id
    assert economy.calls == 3
    assert quality.calls == 1

    route_a_only = await gateway.invoke(
        _request(
            command_id="route-a-only",
            idempotency_key="route-a-only",
            context_items=context,
            allowed_adapters=frozenset({"mock-economy"}),
            latency_tier=2,
        )
    )
    assert route_a_only.cache_hit is False
    assert economy.calls == 4


@pytest.mark.anyio
async def test_cache_write_uses_successful_fallback_route() -> None:
    gateway, economy, quality = _gateway(
        economy_behaviors=(
            MockProviderBehavior.TRANSIENT_FAILURE,
            MockProviderBehavior.SUCCESS,
            MockProviderBehavior.SUCCESS,
            MockProviderBehavior.TRANSIENT_FAILURE,
        )
    )
    first = await gateway.invoke(_request(latency_tier=2))
    assert first.route is not None and first.route.model_key == "quality-v1"

    route_a_only = await gateway.invoke(
        _request(
            command_id="fallback-route-a-only",
            idempotency_key="fallback-route-a-only",
            allowed_adapters=frozenset({"mock-economy"}),
            latency_tier=2,
        )
    )
    assert route_a_only.cache_hit is False

    minimal_identity = await gateway.invoke(
        _request(command_id="fallback-hit", idempotency_key="fallback-hit", latency_tier=2)
    )
    assert minimal_identity.cache_hit is False
    assert minimal_identity.route is not None
    assert minimal_identity.route.model_key == "economy-v1"

    gateway.store.cache = {
        key: value
        for key, value in gateway.store.cache.items()
        if value.route is not None and value.route.model_key == "quality-v1"
    }

    fallback_identity = await gateway.invoke(
        _request(
            command_id="eligible-fallback-hit",
            idempotency_key="eligible-fallback-hit",
            latency_tier=2,
        )
    )
    assert fallback_identity.cache_hit is True
    assert fallback_identity.route is not None
    assert fallback_identity.route.model_key == "quality-v1"
    assert economy.calls == 4
    assert quality.calls == 1


@pytest.mark.anyio
async def test_structured_original_and_repaired_payloads_obey_output_cap() -> None:
    direct, _, _ = _gateway()
    oversized = await direct.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
            max_output_tokens=2,
            repair_attempts=0,
        )
    )
    assert oversized.result.result_status is ResultStatus.FAILED

    repaired, provider, _ = _gateway(economy_behaviors=(MockProviderBehavior.MALFORMED,))
    result = await repaired.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
            max_output_tokens=2,
        )
    )
    assert provider.calls == 2
    assert result.result.result_status is ResultStatus.FAILED
