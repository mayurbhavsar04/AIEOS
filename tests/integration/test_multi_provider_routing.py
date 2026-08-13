"""Offline cross-provider routing/failover conformance for Milestone 6 Phase 4."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.ai_gateway import (
    AIInvocationRequest,
    AIUsage,
    ModelCatalogEntry,
    ProviderFailure,
    ProviderResult,
    ReferenceAIGateway,
    ReferenceGatewayStore,
)
from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.security_support import ScopeAuthorizer
from aieos.testing import DeterministicClock, DeterministicIdentifiers


class ChargedTransientProvider(DeterministicMockProvider):
    async def invoke(self, **kwargs: object) -> ProviderResult:
        self.calls += 1
        raise ProviderFailure(
            "AI_PROVIDER_TRANSIENT_FAILURE",
            retryable=True,
            usage=AIUsage(input_tokens=20, output_tokens=10),
        )


class AmbiguousProvider(DeterministicMockProvider):
    async def invoke(self, **kwargs: object) -> ProviderResult:
        self.calls += 1
        raise ProviderFailure("AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False)


class DeadlineProvider(DeterministicMockProvider):
    async def invoke(self, **kwargs: object) -> ProviderResult:
        import asyncio

        self.calls += 1
        await asyncio.sleep(0.05)
        return ProviderResult("late", AIUsage(1, 1))


class ReasoningProvider(DeterministicMockProvider):
    async def invoke(self, **kwargs: object) -> ProviderResult:
        self.calls += 1
        return ProviderResult("reasoned", AIUsage(10, 2, cached_tokens=4, reasoning_tokens=20))


class RateLimitedProvider(DeterministicMockProvider):
    async def invoke(self, **kwargs: object) -> ProviderResult:
        self.calls += 1
        raise ProviderFailure("AI_PROVIDER_RATE_LIMITED", retryable=True)


def request(**changes: object) -> AIInvocationRequest:
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "capability_contract_version_id": "text-v1",
        "prompt": "The same canonical request",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "correlation_id": "correlation-1",
        "causation_id": "decision-1",
        "authorization": AuthorizationContext(
            "actor", frozenset({"ai.invoke"}), "tenant-a", "workspace-a", "policy", "v1"
        ),
        "command_id": "command-1",
        "idempotency_key": "idem-1",
        "max_total_cost": Decimal("1"),
        "latency_tier": 2,
        "cache_allowed": False,
    }
    values.update(changes)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def gateway(
    *,
    openai: DeterministicMockProvider | None = None,
    gemini: DeterministicMockProvider | None = None,
    openai_cost: str = "0.000001",
    gemini_cost: str = "0.000002",
    openai_available: bool = True,
    gemini_available: bool = True,
    gemini_capabilities: frozenset[str] = frozenset({"text", "structured", "stream"}),
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, DeterministicMockProvider]:
    identifiers = DeterministicIdentifiers()
    openai = openai or DeterministicMockProvider("openai-responses", prefix="OpenAI")
    gemini = gemini or DeterministicMockProvider("gemini-generate-content", prefix="Gemini")
    runtime = ReferenceAIGateway(
        clock=DeterministicClock(datetime(2026, 8, 12, tzinfo=UTC)),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=ReferenceGatewayStore(),
        catalog=(
            ModelCatalogEntry(
                "economy-text-openai-v1",
                "openai-responses",
                frozenset({"text", "structured", "stream"}),
                400_000,
                128_000,
                1,
                1,
                Decimal(openai_cost),
                Decimal(openai_cost),
                "openai-price-v1",
                residencies=frozenset({"any", "us"}),
                data_handling=frozenset({"internal", "zdr-eligible"}),
                available=openai_available,
            ),
            ModelCatalogEntry(
                "economy-text-gemini-v1",
                "gemini-generate-content",
                gemini_capabilities,
                1_048_576,
                65_536,
                1,
                1,
                Decimal(gemini_cost),
                Decimal(gemini_cost),
                "gemini-price-v1",
                residencies=frozenset({"any"}),
                data_handling=frozenset({"internal", "paid-no-training"}),
                available=gemini_available,
            ),
        ),
        adapters={"openai-responses": openai, "gemini-generate-content": gemini},
    )
    return runtime, openai, gemini


@pytest.mark.anyio
async def test_same_request_routes_to_either_provider_from_catalog_state() -> None:
    first, _, _ = gateway(openai_cost="0.000001", gemini_cost="0.000002")
    openai_result = await first.invoke(request())
    second, _, _ = gateway(openai_cost="0.000003", gemini_cost="0.000002")
    gemini_result = await second.invoke(request())
    assert openai_result.route is not None
    assert gemini_result.route is not None
    assert openai_result.route.adapter_key == "openai-responses"
    assert gemini_result.route.adapter_key == "gemini-generate-content"
    assert openai_result.result.result_status is gemini_result.result.result_status
    assert openai_result.usage is not None and gemini_result.usage is not None


@pytest.mark.anyio
async def test_constraints_beat_cheaper_provider_and_explanation_is_deterministic() -> None:
    runtime, _, _ = gateway(openai_cost="0.000003", gemini_cost="0.000001")
    result = await runtime.invoke(
        request(residency="us", required_data_handling=frozenset({"zdr-eligible"}))
    )
    assert result.route is not None and result.route.adapter_key == "openai-responses"
    assert result.route.excluded["economy-text-gemini-v1"] == "residency"
    assert result.route.reason == "cheapest capable model satisfying all hard constraints"
    assert result.route.decision_reference.startswith("route:")


@pytest.mark.anyio
async def test_unsupported_capability_and_unhealthy_catalog_state_are_filtered() -> None:
    runtime, _, _ = gateway(openai_available=False, gemini_capabilities=frozenset({"text"}))
    result = await runtime.invoke(request(required_capabilities=frozenset({"structured"})))
    assert result.result.result_status is ResultStatus.FAILED
    assert result.route is None


@pytest.mark.anyio
async def test_openai_to_gemini_and_reverse_failover_preserve_one_invocation() -> None:
    openai = DeterministicMockProvider(
        "openai-responses",
        prefix="OpenAI",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    runtime, _, gemini = gateway(openai=openai)
    result = await runtime.invoke(request(max_provider_attempts=2))
    assert result.result.result_status is ResultStatus.SUCCEEDED
    assert openai.calls == 1 and gemini.calls == 1
    assert len(runtime.store.attempts[result.ai_invocation_id]) == 2

    gemini_first = DeterministicMockProvider(
        "gemini-generate-content",
        prefix="Gemini",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    reverse, openai_second, _ = gateway(
        gemini=gemini_first, openai_cost="0.000003", gemini_cost="0.000001"
    )
    reverse_result = await reverse.invoke(request(max_provider_attempts=2))
    assert reverse_result.result.result_status is ResultStatus.SUCCEEDED
    assert gemini_first.calls == 1 and openai_second.calls == 1


@pytest.mark.anyio
async def test_cumulative_spend_stops_fallback_and_never_resets() -> None:
    charged = ChargedTransientProvider("openai-responses", prefix="OpenAI")
    runtime, _, gemini = gateway(
        openai=charged,
        openai_cost="0.000001",
        gemini_cost="0.000002",
    )
    result = await runtime.invoke(
        request(max_total_cost=Decimal("0.00010"), max_output_tokens=20, max_provider_attempts=2)
    )
    assert result.result.result_status is ResultStatus.FAILED
    assert gemini.calls == 0
    assert result.error is not None and result.error.error_code == "AI_FALLBACK_BUDGET_EXHAUSTED"
    assert result.usage == AIUsage(20, 10)


@pytest.mark.anyio
async def test_ambiguous_effect_blocks_cross_provider_failover() -> None:
    ambiguous = AmbiguousProvider("openai-responses", prefix="OpenAI")
    runtime, _, gemini = gateway(openai=ambiguous)
    result = await runtime.invoke(request(max_provider_attempts=2))
    assert result.result.result_status is ResultStatus.FAILED
    assert gemini.calls == 0
    assert result.error is not None
    assert result.error.error_code == "AI_PROVIDER_EFFECT_AMBIGUOUS"


@pytest.mark.anyio
async def test_max_attempts_prevents_provider_loop() -> None:
    openai = DeterministicMockProvider("openai-responses", prefix="OpenAI", transient_failures=3)
    gemini = DeterministicMockProvider(
        "gemini-generate-content", prefix="Gemini", transient_failures=3
    )
    runtime, _, _ = gateway(openai=openai, gemini=gemini)
    result = await runtime.invoke(request(max_provider_attempts=2))
    assert result.result.result_status is ResultStatus.FAILED
    assert openai.calls == 1 and gemini.calls == 1
    assert len(runtime.store.attempts[result.ai_invocation_id]) == 2


@pytest.mark.anyio
async def test_gateway_deadline_after_dispatch_blocks_cross_provider_failover() -> None:
    first = DeadlineProvider("openai-responses", prefix="OpenAI")
    runtime, _, gemini = gateway(openai=first)
    result = await runtime.invoke(
        request(deadline=datetime(2026, 8, 12, 0, 0, 0, 1000, tzinfo=UTC))
    )
    assert result.result.result_status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "AI_PROVIDER_EFFECT_AMBIGUOUS"
    assert first.calls == 1 and gemini.calls == 0


@pytest.mark.anyio
async def test_reasoning_tokens_consume_fallback_budget() -> None:
    first = ReasoningProvider("openai-responses", prefix="OpenAI")
    runtime, _, _ = gateway(openai=first, openai_cost="0.000002", gemini_cost="0.000003")
    result = await runtime.invoke(request(max_total_cost=Decimal("0.001")))
    assert result.result.result_status is ResultStatus.SUCCEEDED
    reservation = runtime.store.reservations[result.ai_invocation_id]
    assert reservation.actual == Decimal("0.000064")
    assert result.usage is not None and result.usage.reasoning_tokens == 20


@pytest.mark.anyio
async def test_rate_limited_model_enters_thread_safe_cooldown() -> None:
    openai = RateLimitedProvider("openai-responses", prefix="OpenAI")
    runtime, _, gemini = gateway(openai=openai)
    first = await runtime.invoke(request(idempotency_key="cooldown-1", command_id="cooldown-1"))
    assert first.result.result_status is ResultStatus.SUCCEEDED
    second = await runtime.invoke(request(idempotency_key="cooldown-2", command_id="cooldown-2"))
    assert second.result.result_status is ResultStatus.SUCCEEDED
    assert openai.calls == 1
    assert gemini.calls == 2
    assert second.route is not None and second.route.adapter_key == "gemini-generate-content"
