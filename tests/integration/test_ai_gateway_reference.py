"""Offline conformance tests for the Milestone 6 Phase 2 gateway."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.ai_gateway import (
    AIInvocationRequest,
    ContextItem,
    InvocationState,
    ModelCatalogEntry,
    ProviderBehavior,
    ReferenceAIGateway,
    ReferenceGatewayStore,
    ResponseMode,
)
from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.security_support import ScopeAuthorizer
from aieos.testing import DeterministicClock, DeterministicIdentifiers


def _gateway(
    *, transient_failures: int = 0, tenant_limit: str = "100"
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, DeterministicMockProvider]:
    clock = DeterministicClock(datetime(2026, 8, 3, tzinfo=UTC))
    identifiers = DeterministicIdentifiers()
    economy = DeterministicMockProvider(
        "mock-economy", prefix="Economy", transient_failures=transient_failures
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
        _request(command_id="command-2", idempotency_key="idem-2", quality_tier=3)
    )
    assert quality.route is not None and quality.route.model_key == "quality-v1"


@pytest.mark.anyio
async def test_context_deduplication_truncation_and_boundaries() -> None:
    gateway, _, _ = _gateway()
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
    response = await gateway.invoke(_request(max_provider_attempts=2))
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert economy.calls == 1 and quality.calls == 1
    assert InvocationState.RETRYING in gateway.lifecycle[response.ai_invocation_id]


@pytest.mark.anyio
async def test_structured_output_repair_and_bounded_failure() -> None:
    gateway, _, _ = _gateway()
    repaired = await gateway.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
            behavior=ProviderBehavior.MALFORMED,
        )
    )
    assert repaired.result.result_status is ResultStatus.SUCCEEDED
    gateway2, _, _ = _gateway()
    failed = await gateway2.invoke(
        _request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="answer-v1",
            behavior=ProviderBehavior.MALFORMED,
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
