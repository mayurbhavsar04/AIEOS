"""Mandatory real-PostgreSQL durability and recovery matrix."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.adapters.event_bus_in_process import InProcessEventBus
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.adapters.persistence_postgres import (
    PostgresAIGatewayStore,
    PostgresDatabase,
    PostgresOutboxRelay,
    PostgresOutboxStore,
    PostgresProviderEffectBoundary,
    PostgresWorkflowRepository,
)
from aieos.adapters.persistence_postgres.models import (
    AIGatewayAttemptRow,
    AIGatewayBudgetRow,
    AIGatewayInvocationRow,
    AIGatewayProviderEffectRow,
    Base,
    CommandIdempotencyRow,
    DecisionEvidenceRow,
    DeliveryReceiptRow,
    ExecutionRow,
    MemoryRecordRow,
    OutboxEventRow,
    OutcomeRow,
    WorkflowRow,
    WorkflowStepRow,
)
from aieos.ai_gateway import (
    AIInvocationRequest,
    AIUsage,
    ContextItem,
    ModelCatalogEntry,
    ProviderResult,
    ReferenceAIGateway,
    ResponseMode,
    RouteDecision,
)
from aieos.ai_gateway.gateway import CachedContent, ExecutionOwnershipLost, ProviderFailure
from aieos.contracts import AuthorizationContext, ResultEnvelope, ResultStatus
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.contracts.events import EventEnvelope, EventMetadata
from aieos.security_support import ScopeAuthorizer
from aieos.testing import DeterministicClock, DeterministicIdentifiers
from aieos_api.composition import CompositionRoot, compose
from aieos_api.settings import HostSettings, RuntimeAdapter

pytestmark = [pytest.mark.integration, pytest.mark.postgres_required, pytest.mark.anyio]

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "ai_gateway_attempts",
    "ai_gateway_budgets",
    "ai_gateway_cache",
    "ai_gateway_invocations",
    "ai_gateway_provider_effects",
    "ai_gateway_usage_ledger",
    "alembic_version",
    "command_idempotency",
    "decision_evidence",
    "delivery_receipts",
    "executions",
    "memory_records",
    "outbox_events",
    "outcomes",
    "workflow_steps",
    "workflows",
}


def database_url() -> str:
    url = os.environ.get("AIEOS_TEST_DATABASE_URL")
    if url is None:
        if os.environ.get("CI"):
            pytest.fail("mandatory AIEOS_TEST_DATABASE_URL is not configured in CI")
        pytest.skip("live PostgreSQL suite not executed: set AIEOS_TEST_DATABASE_URL")
    return url


async def reset(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        await session.execute(
            text(
                "TRUNCATE delivery_receipts, outbox_events, outcomes, "
                "command_idempotency, executions, workflow_steps, workflows, "
                "decision_evidence, memory_records, ai_gateway_usage_ledger, "
                "ai_gateway_attempts, ai_gateway_budgets, ai_gateway_cache, "
                "ai_gateway_invocations CASCADE"
            )
        )


def ai_request(**overrides: object) -> AIInvocationRequest:
    values: dict[str, object] = {
        "execution_id": "execution-ai-1",
        "capability_contract_version_id": "text-v1",
        "prompt": "durable gateway",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-ai-1",
        "causation_id": "decision-ai-1",
        "authorization": AuthorizationContext(
            "actor-1",
            frozenset({"ai.invoke"}),
            "tenant-1",
            "workspace-1",
            "policy-1",
            "v1",
        ),
        "command_id": "command-ai-1",
        "idempotency_key": "idem-ai-1",
        "max_total_cost": Decimal("1"),
    }
    values.update(overrides)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def durable_ai_gateway(
    database: PostgresDatabase,
    *,
    provider: DeterministicMockProvider | None = None,
    clock: Any | None = None,
    execution_lease: timedelta = timedelta(seconds=30),
    heartbeat_interval: float = 10.0,
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, PostgresAIGatewayStore]:
    effective_clock: Any = clock or DeterministicClock(datetime(2026, 8, 10, tzinfo=UTC))
    identifiers = DeterministicIdentifiers()
    provider = provider or DeterministicMockProvider("mock", prefix="Durable")
    provider.use_effect_boundary(PostgresProviderEffectBoundary(database))
    store = PostgresAIGatewayStore(database)
    gateway = ReferenceAIGateway(
        clock=effective_clock,
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=store,
        catalog=(
            ModelCatalogEntry(
                "model-v1",
                "mock",
                frozenset({"text", "stream", "structured"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000001"),
                Decimal("0.000002"),
                "price-v1",
            ),
        ),
        adapters={"mock": provider},
        execution_lease=execution_lease,
        heartbeat_interval=heartbeat_interval,
    )
    return gateway, provider, store


def durable_multi_provider_gateway(
    database: PostgresDatabase,
    *,
    first: DeterministicMockProvider | None = None,
    second: DeterministicMockProvider | None = None,
    store: PostgresAIGatewayStore | None = None,
) -> tuple[ReferenceAIGateway, DeterministicMockProvider, DeterministicMockProvider]:
    identifiers = DeterministicIdentifiers()
    first = first or DeterministicMockProvider("openai-responses", prefix="OpenAI")
    second = second or DeterministicMockProvider("gemini-generate-content", prefix="Gemini")
    boundary = PostgresProviderEffectBoundary(database)
    first.use_effect_boundary(boundary)
    second.use_effect_boundary(boundary)
    gateway = ReferenceAIGateway(
        clock=DeterministicClock(datetime(2026, 8, 12, tzinfo=UTC)),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=store or PostgresAIGatewayStore(database),
        catalog=(
            ModelCatalogEntry(
                "openai-model-v1",
                "openai-responses",
                frozenset({"text"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000001"),
                Decimal("0.000002"),
                "openai-price-v1",
            ),
            ModelCatalogEntry(
                "gemini-model-v1",
                "gemini-generate-content",
                frozenset({"text"}),
                4096,
                512,
                1,
                1,
                Decimal("0.000002"),
                Decimal("0.000003"),
                "gemini-price-v1",
            ),
        ),
        adapters={first.key: first, second.key: second},
    )
    return gateway, first, second


async def test_ai_gateway_acceptance_restart_replay_and_terminal_recovery(
    database: PostgresDatabase,
) -> None:
    first, first_provider, _ = durable_ai_gateway(database)
    response = await first.invoke(ai_request())
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert first_provider.calls == 1

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    replay = await restarted.invoke(ai_request(command_id="redelivered-command"))
    assert replay.ai_invocation_id == response.ai_invocation_id
    assert replay.result.result_id == response.result.result_id
    assert restarted_provider.calls == 0


async def test_multi_provider_attempt_sequence_spend_and_terminal_survive_restart(
    database: PostgresDatabase,
) -> None:
    first = DeterministicMockProvider(
        "openai-responses",
        prefix="OpenAI",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    gateway, _, second = durable_multi_provider_gateway(database, first=first)
    response = await gateway.invoke(
        ai_request(idempotency_key="multi-provider-durable", max_provider_attempts=2)
    )
    assert response.result.result_status is ResultStatus.SUCCEEDED
    assert first.calls == 1 and second.calls == 1
    async with database.transaction() as session:
        attempts = list(
            await session.scalars(
                select(AIGatewayAttemptRow)
                .where(AIGatewayAttemptRow.ai_invocation_id == response.ai_invocation_id)
                .order_by(AIGatewayAttemptRow.attempt_number)
            )
        )
        budget = await session.scalar(
            select(AIGatewayBudgetRow).where(
                AIGatewayBudgetRow.ai_invocation_id == response.ai_invocation_id
            )
        )
    assert [(row.adapter_key, row.state) for row in attempts] == [
        ("openai-responses", "failed"),
        ("gemini-generate-content", "completed"),
    ]
    assert budget is not None and budget.actual_amount is not None
    terminal_result_id = response.result.result_id

    restarted, restarted_first, restarted_second = durable_multi_provider_gateway(database)
    replay = await restarted.invoke(
        ai_request(
            command_id="multi-provider-redelivery",
            idempotency_key="multi-provider-durable",
            max_provider_attempts=2,
        )
    )
    assert replay.ai_invocation_id == response.ai_invocation_id
    assert replay.result.result_id == terminal_result_id
    assert restarted_first.calls == 0 and restarted_second.calls == 0
    assert replay.usage == response.usage


async def test_multi_provider_confirmed_failure_resumes_after_checkpoint_crash(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashAfterFailureCheckpoint(PostgresAIGatewayStore):
        crashed = False

        async def checkpoint(self, invocation: Any) -> None:
            if invocation.next_provider_attempt == 2 and not self.crashed:
                self.crashed = True
                raise InjectedCrash
            await super().checkpoint(invocation)

    first = DeterministicMockProvider(
        "openai-responses",
        prefix="OpenAI",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    gateway, _, _ = durable_multi_provider_gateway(
        database,
        first=first,
        store=CrashAfterFailureCheckpoint(database),
    )
    request = ai_request(
        idempotency_key="multi-provider-failure-checkpoint", max_provider_attempts=2
    )
    with pytest.raises(InjectedCrash):
        await gateway.invoke(request)

    restarted, restarted_first, restarted_second = durable_multi_provider_gateway(database)
    recovered = await restarted.invoke(replace(request, command_id="failure-checkpoint-replay"))
    assert recovered.result.result_status is ResultStatus.SUCCEEDED
    assert first.calls == 1
    assert restarted_first.calls == 0 and restarted_second.calls == 1
    assert recovered.usage is not None and recovered.usage.input_tokens > 0


async def test_multi_provider_completed_second_effect_replays_after_accounting_crash(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashBeforeSecondAccounting(PostgresAIGatewayStore):
        async def record_attempt(self, invocation_id: str, **values: Any) -> None:
            if int(values["attempt_number"]) == 2 and values["state"] == "completed":
                raise InjectedCrash
            await super().record_attempt(invocation_id, **values)

    first = DeterministicMockProvider(
        "openai-responses",
        prefix="OpenAI",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    second = DeterministicMockProvider("gemini-generate-content", prefix="Gemini")
    gateway, _, _ = durable_multi_provider_gateway(
        database,
        first=first,
        second=second,
        store=CrashBeforeSecondAccounting(database),
    )
    request = ai_request(idempotency_key="multi-provider-second-effect", max_provider_attempts=2)
    with pytest.raises(InjectedCrash):
        await gateway.invoke(request)

    restarted, restarted_first, restarted_second = durable_multi_provider_gateway(database)
    recovered = await restarted.invoke(replace(request, command_id="second-effect-replay"))
    assert recovered.result.result_status is ResultStatus.SUCCEEDED
    assert first.calls == 1 and second.calls == 1
    assert restarted_first.calls == 0 and restarted_second.calls == 0
    async with database.transaction() as session:
        attempts = list(
            await session.scalars(
                select(AIGatewayAttemptRow)
                .where(AIGatewayAttemptRow.ai_invocation_id == recovered.ai_invocation_id)
                .order_by(AIGatewayAttemptRow.attempt_number)
            )
        )
    assert [attempt.state for attempt in attempts] == ["failed", "completed"]


async def test_ai_gateway_restart_does_not_probe_unapproved_expanded_cache(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    request = ai_request(
        idempotency_key="expanded-cache-seed",
        context_items=(
            ContextItem("minimal", "v1", "minimum", 10, "initial"),
            ContextItem("expanded", "v1", "expanded evidence", 1, "escalation"),
        ),
    )
    _, expanded_tokens, expanded_digest = gateway._assemble(  # pyright: ignore[reportPrivateUsage]
        request, stage=1
    )
    expanded_route = gateway._route(  # pyright: ignore[reportPrivateUsage]
        request, expanded_tokens
    )
    expanded_key = gateway._cache_key(  # pyright: ignore[reportPrivateUsage]
        request, expanded_digest, expanded_route
    )
    await store.cache_content(
        request.tenant_id,
        request.workspace_id,
        expanded_key,
        CachedContent(
            "expanded-only",
            AIUsage(10, 3),
            datetime(2026, 8, 10, 0, 10, tzinfo=UTC),
            "seeded-expanded-invocation",
            expanded_route,
        ),
    )

    restarted, provider, _ = durable_ai_gateway(database)
    response = await restarted.invoke(request)
    assert response.cache_hit is False
    assert response.content != "expanded-only"
    assert provider.calls == 1


async def test_ai_gateway_concurrent_duplicate_admission_survives_workers(
    database: PostgresDatabase,
) -> None:
    first, _, _ = durable_ai_gateway(database)
    second, _, _ = durable_ai_gateway(database)
    one, two = await asyncio.gather(
        first.accept(ai_request()),
        second.accept(ai_request(command_id="redelivery")),
    )
    assert one.ai_invocation_id == two.ai_invocation_id
    assert one.replay is not two.replay


async def test_ai_gateway_concurrent_invoke_has_one_durable_execution_owner(
    database: PostgresDatabase,
) -> None:
    first, first_provider, _ = durable_ai_gateway(database)
    second, second_provider, _ = durable_ai_gateway(database)
    one, two = await asyncio.gather(
        first.invoke(ai_request(idempotency_key="concurrent-execution")),
        second.invoke(
            ai_request(
                command_id="redelivered-concurrent-execution",
                idempotency_key="concurrent-execution",
            )
        ),
    )
    assert first_provider.calls + second_provider.calls == 1
    assert one.ai_invocation_id == two.ai_invocation_id
    assert one.result.result_id == two.result.result_id


async def test_ai_gateway_stale_execution_lease_is_reclaimable(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="stale-execution"))
    now = datetime(2026, 8, 10, tzinfo=UTC)
    first_generation = await store.claim_execution(
        accepted.ai_invocation_id,
        owner="crashed-worker",
        now=now,
        lease=timedelta(seconds=30),
    )
    assert first_generation == 1
    competing = PostgresAIGatewayStore(database)
    assert (
        await competing.claim_execution(
            accepted.ai_invocation_id,
            owner="early-worker",
            now=now + timedelta(seconds=29),
            lease=timedelta(seconds=30),
        )
        is None
    )
    reclaimed = await competing.claim_execution(
        accepted.ai_invocation_id,
        owner="recovery-worker",
        now=now + timedelta(seconds=31),
        lease=timedelta(seconds=30),
    )
    assert reclaimed == 2


async def test_ai_gateway_lease_renewal_blocks_reclaim_beyond_original_ttl(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="renewed-execution"))
    started = datetime(2026, 8, 10, tzinfo=UTC)
    generation = await store.claim_execution(
        accepted.ai_invocation_id,
        owner="slow-worker",
        now=started,
        lease=timedelta(seconds=30),
    )
    assert generation == 1
    assert await store.renew_execution(
        accepted.ai_invocation_id,
        owner="slow-worker",
        generation=generation,
        now=started + timedelta(seconds=25),
        lease=timedelta(seconds=30),
    )
    competitor = PostgresAIGatewayStore(database)
    assert (
        await competitor.claim_execution(
            accepted.ai_invocation_id,
            owner="competitor",
            now=started + timedelta(seconds=31),
            lease=timedelta(seconds=30),
        )
        is None
    )


async def test_ai_gateway_slow_active_provider_is_heartbeat_renewed(
    database: PostgresDatabase,
) -> None:
    class RealtimeClock:
        def now(self) -> datetime:
            return datetime.now(UTC)

    class SlowProvider(DeterministicMockProvider):
        async def invoke(self, **values: Any) -> ProviderResult:
            await asyncio.sleep(0.25)
            return await super().invoke(**values)

    lease = timedelta(seconds=0.1)
    first_provider = SlowProvider("mock", prefix="Slow")
    first, _, _ = durable_ai_gateway(
        database,
        provider=first_provider,
        clock=RealtimeClock(),
        execution_lease=lease,
        heartbeat_interval=0.02,
    )
    second, second_provider, _ = durable_ai_gateway(
        database,
        clock=RealtimeClock(),
        execution_lease=lease,
        heartbeat_interval=0.02,
    )
    first_task = asyncio.create_task(
        first.invoke(ai_request(idempotency_key="slow-heartbeat-provider"))
    )
    await asyncio.sleep(0.14)
    second_task = asyncio.create_task(
        second.invoke(
            ai_request(
                command_id="slow-heartbeat-competitor",
                idempotency_key="slow-heartbeat-provider",
            )
        )
    )
    one, two = await asyncio.gather(first_task, second_task)
    assert one.result.result_status is ResultStatus.SUCCEEDED
    assert two.result.result_id == one.result.result_id
    assert first_provider.calls == 1
    assert second_provider.calls == 0


async def test_ai_gateway_reclaim_increments_generation_and_fences_stale_effect(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="fenced-effect"))
    invocation = await store.load(accepted.ai_invocation_id)
    invocation.route = RouteDecision(
        "route:test",
        "model-v1",
        "mock",
        ("model-v1",),
        {},
        Decimal("0.01"),
        "test route",
    )
    started = datetime(2026, 8, 10, tzinfo=UTC)
    first = await store.claim_execution(
        accepted.ai_invocation_id,
        owner="first",
        now=started,
        lease=timedelta(seconds=1),
    )
    assert first == 1
    invocation.execution_owner = "first"
    invocation.claim_generation = first
    await store.checkpoint(invocation)
    second_store = PostgresAIGatewayStore(database)
    second = await second_store.claim_execution(
        accepted.ai_invocation_id,
        owner="second",
        now=started + timedelta(seconds=2),
        lease=timedelta(seconds=30),
    )
    assert second == 2
    with pytest.raises(ExecutionOwnershipLost):
        await store.record_provider_effect(
            accepted.ai_invocation_id,
            effect_key=f"{accepted.ai_invocation_id}:provider:1",
            result=ProviderResult("stale", AIUsage(1, 1)),
            owner="first",
            generation=first,
        )
    with pytest.raises(ExecutionOwnershipLost):
        await store.record_usage(
            accepted.ai_invocation_id,
            usage=AIUsage(1, 1),
            cost=Decimal("0.01"),
            event_key="stale-accounting",
            kind="actual",
            final=True,
            owner="first",
            generation=first,
        )
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        await store.checkpoint(invocation)
    async with database.transaction() as session:
        effects = await session.scalar(select(func.count()).select_from(AIGatewayAttemptRow))
    assert effects == 0


async def test_ai_gateway_terminal_intent_is_fenced_and_only_valid_generation_recovers(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="fenced-terminal-intent"))
    started = datetime(2026, 8, 10, tzinfo=UTC)
    first = await store.claim_execution(
        accepted.ai_invocation_id,
        owner="stale-terminal-worker",
        now=started,
        lease=timedelta(seconds=1),
    )
    assert first == 1
    stale = await store.load(accepted.ai_invocation_id)
    stale.execution_owner = "stale-terminal-worker"
    stale.claim_generation = first
    stale_response = gateway._failure(  # pyright: ignore[reportPrivateUsage]
        stale, ProviderFailure("AI_STALE_TERMINAL", retryable=False)
    )
    stale.terminal = None
    stale.terminal_intent = stale_response

    winning_store = PostgresAIGatewayStore(database)
    second = await winning_store.claim_execution(
        accepted.ai_invocation_id,
        owner="winning-terminal-worker",
        now=started + timedelta(seconds=2),
        lease=timedelta(seconds=30),
    )
    assert second == 2
    with pytest.raises(ExecutionOwnershipLost, match="stale terminal intent"):
        await store.checkpoint_terminal_intent(stale)

    winner = await winning_store.load(accepted.ai_invocation_id)
    winner.execution_owner = "winning-terminal-worker"
    winner.claim_generation = second
    winning_response = gateway._failure(  # pyright: ignore[reportPrivateUsage]
        winner, ProviderFailure("AI_VALID_TERMINAL", retryable=False)
    )
    winner.terminal = None
    winner.terminal_intent = winning_response
    stale_race, winning_race = await asyncio.gather(
        store.checkpoint_terminal_intent(stale),
        winning_store.checkpoint_terminal_intent(winner),
        return_exceptions=True,
    )
    assert isinstance(stale_race, ExecutionOwnershipLost)
    assert winning_race is None

    stale.terminal_intent = stale_response
    with pytest.raises(ExecutionOwnershipLost, match="stale terminal intent"):
        await store.checkpoint_terminal_intent(stale)
    async with database.transaction() as session:
        row = await session.scalar(
            select(AIGatewayInvocationRow).where(
                AIGatewayInvocationRow.ai_invocation_id == accepted.ai_invocation_id
            )
        )
        assert row is not None
        assert row.terminal_intent_owner == "winning-terminal-worker"
        assert row.terminal_intent_generation == second
        await session.execute(
            update(AIGatewayInvocationRow)
            .where(AIGatewayInvocationRow.ai_invocation_id == accepted.ai_invocation_id)
            .values(execution_lease_expires_at=started)
        )

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    recovered = await restarted.invoke(
        ai_request(
            command_id="fenced-terminal-recovery",
            idempotency_key="fenced-terminal-intent",
        )
    )
    assert recovered.result.result_id == winning_response.result.result_id
    assert recovered.result.result_id != stale_response.result.result_id
    assert restarted_provider.calls == 0
    async with database.transaction() as session:
        row = await session.scalar(
            select(AIGatewayInvocationRow).where(
                AIGatewayInvocationRow.ai_invocation_id == accepted.ai_invocation_id
            )
        )
        assert row is not None
        assert row.terminal_result_id == winning_response.result.result_id


async def test_ai_gateway_concurrent_reclaim_race_has_one_generation_winner(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="reclaim-race"))
    started = datetime(2026, 8, 10, tzinfo=UTC)
    assert (
        await store.claim_execution(
            accepted.ai_invocation_id,
            owner="crashed",
            now=started,
            lease=timedelta(seconds=1),
        )
        == 1
    )
    one, two = await asyncio.gather(
        PostgresAIGatewayStore(database).claim_execution(
            accepted.ai_invocation_id,
            owner="reclaimer-one",
            now=started + timedelta(seconds=2),
            lease=timedelta(seconds=30),
        ),
        PostgresAIGatewayStore(database).claim_execution(
            accepted.ai_invocation_id,
            owner="reclaimer-two",
            now=started + timedelta(seconds=2),
            lease=timedelta(seconds=30),
        ),
    )
    assert sorted(value for value in (one, two) if value is not None) == [2]


async def test_ai_gateway_checkpoint_failure_returns_normalized_recoverable_terminal(
    database: PostgresDatabase,
) -> None:
    class FailFirstCheckpointStore(PostgresAIGatewayStore):
        failed = False

        async def checkpoint(self, invocation: Any) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected persistence checkpoint failure")
            await super().checkpoint(invocation)

    gateway, provider, _ = durable_ai_gateway(database)
    failing = FailFirstCheckpointStore(database)
    gateway.store = failing
    failed = await gateway.invoke(ai_request(idempotency_key="checkpoint-failure"))
    assert failed.result.result_status is ResultStatus.FAILED
    assert provider.calls == 0

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    recovered = await restarted.invoke(
        ai_request(
            command_id="checkpoint-failure-recovery",
            idempotency_key="checkpoint-failure",
        )
    )
    assert recovered.result.result_id == failed.result.result_id
    assert restarted_provider.calls == 0


async def test_ai_gateway_restart_reuses_completed_provider_effect_after_crash(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashAfterEffectStore(PostgresAIGatewayStore):
        crashed = False

        async def record_attempt(self, invocation_id: str, **values: Any) -> None:
            if not self.crashed:
                self.crashed = True
                raise InjectedCrash
            await super().record_attempt(invocation_id, **values)

    gateway, provider, _ = durable_ai_gateway(database)
    gateway.store = CrashAfterEffectStore(database)
    with pytest.raises(InjectedCrash):
        await gateway.invoke(ai_request(idempotency_key="provider-effect-crash"))
    assert provider.calls == 1

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    recovered = await restarted.invoke(
        ai_request(
            command_id="provider-effect-recovery",
            idempotency_key="provider-effect-crash",
        )
    )
    assert recovered.result.result_status is ResultStatus.SUCCEEDED
    assert restarted_provider.calls == 0
    async with database.transaction() as session:
        budget = await session.get(
            AIGatewayBudgetRow, ("tenant-1", "workspace-1", recovered.ai_invocation_id)
        )
        assert budget is not None
        assert budget.state == "committed"


async def test_ai_gateway_stream_crash_after_durable_chunk_recovers_once(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashAfterChunkStore(PostgresAIGatewayStore):
        crashed = False

        async def checkpoint(self, invocation: Any) -> None:
            await super().checkpoint(invocation)
            if invocation.stream_content and not self.crashed:
                self.crashed = True
                raise InjectedCrash

    gateway, provider, _ = durable_ai_gateway(database)
    gateway.store = CrashAfterChunkStore(database)
    with pytest.raises(InjectedCrash):
        async for _ in gateway.stream(ai_request(idempotency_key="stream-chunk-crash")):
            pass
    assert provider.calls == 1
    async with database.transaction() as session:
        await session.execute(
            update(AIGatewayInvocationRow)
            .where(AIGatewayInvocationRow.idempotency_key == "stream-chunk-crash")
            .values(execution_lease_expires_at=datetime(2026, 8, 9, tzinfo=UTC))
        )

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    chunks = [
        chunk
        async for chunk in restarted.stream(
            ai_request(
                command_id="stream-chunk-recovery",
                idempotency_key="stream-chunk-crash",
            )
        )
    ]
    terminals = [chunk for chunk in chunks if chunk.kind == "terminal"]
    assert len(terminals) == 1
    assert terminals[0].terminal is not None
    assert terminals[0].terminal.result.result_status is ResultStatus.FAILED
    assert terminals[0].terminal.error is not None
    assert terminals[0].terminal.error.error_code == "AI_PROVIDER_EFFECT_AMBIGUOUS"
    assert restarted_provider.calls == 0


async def test_ai_gateway_stream_terminal_checkpoint_failure_replays_same_result(
    database: PostgresDatabase,
) -> None:
    class FailTerminalCheckpointStore(PostgresAIGatewayStore):
        failed = False

        async def checkpoint(self, invocation: Any) -> None:
            if invocation.terminal is not None and not self.failed:
                self.failed = True
                raise RuntimeError("injected streaming terminal checkpoint failure")
            await super().checkpoint(invocation)

    gateway, _, _ = durable_ai_gateway(database)
    gateway.store = FailTerminalCheckpointStore(database)
    first = [
        chunk
        async for chunk in gateway.stream(ai_request(idempotency_key="stream-terminal-failure"))
    ]
    first_terminal = first[-1].terminal
    assert first_terminal is not None
    assert first_terminal.result.result_status is ResultStatus.SUCCEEDED

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    replay = [
        chunk
        async for chunk in restarted.stream(
            ai_request(
                command_id="stream-terminal-recovery",
                idempotency_key="stream-terminal-failure",
            )
        )
    ]
    assert replay[-1].terminal is not None
    assert replay[-1].terminal.result.result_id == first_terminal.result.result_id
    assert restarted_provider.calls == 0


async def test_ai_gateway_failed_stream_chunk_checkpoint_is_normalized_and_recoverable(
    database: PostgresDatabase,
) -> None:
    class FailUsageCheckpointStore(PostgresAIGatewayStore):
        failed = False

        async def checkpoint(self, invocation: Any) -> None:
            if invocation.stream_usage is not None and not self.failed:
                self.failed = True
                raise RuntimeError("injected stream chunk checkpoint failure")
            await super().checkpoint(invocation)

    gateway, provider, _ = durable_ai_gateway(database)
    gateway.store = FailUsageCheckpointStore(database)
    chunks = [
        chunk
        async for chunk in gateway.stream(
            ai_request(idempotency_key="stream-failed-chunk-checkpoint")
        )
    ]
    terminals = [chunk for chunk in chunks if chunk.kind == "terminal"]
    assert len(terminals) == 1
    terminal = terminals[0].terminal
    assert terminal is not None
    assert terminal.result.result_status is ResultStatus.FAILED
    assert terminal.usage is not None
    assert provider.calls == 1

    restarted, restarted_provider, _ = durable_ai_gateway(database)
    replay = [
        chunk
        async for chunk in restarted.stream(
            ai_request(
                command_id="stream-failed-chunk-recovery",
                idempotency_key="stream-failed-chunk-checkpoint",
            )
        )
    ]
    replay_terminals = [chunk for chunk in replay if chunk.kind == "terminal"]
    assert len(replay_terminals) == 1
    assert replay_terminals[0].terminal is not None
    assert replay_terminals[0].terminal.result.result_id == terminal.result.result_id
    assert replay_terminals[0].terminal.usage == terminal.usage
    assert restarted_provider.calls == 0


async def test_ai_gateway_structured_repair_crash_before_effect_recording_reuses_effect(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashBeforeRepairEffectRecord(PostgresAIGatewayStore):
        async def record_provider_effect(self, invocation_id: str, **values: Any) -> None:
            if ":repair:" in str(values["effect_key"]):
                raise InjectedCrash
            await super().record_provider_effect(invocation_id, **values)

    provider = DeterministicMockProvider(
        "mock", prefix="Durable", behaviors=(MockProviderBehavior.MALFORMED,)
    )
    gateway, _, _ = durable_ai_gateway(database, provider=provider)
    gateway.store = CrashBeforeRepairEffectRecord(database)
    request = ai_request(
        idempotency_key="repair-pre-record-crash",
        response_mode=ResponseMode.STRUCTURED,
        output_schema_ref="answer-v1",
    )
    with pytest.raises(InjectedCrash):
        await gateway.invoke(request)
    assert provider.calls == 2
    async with database.transaction() as session:
        await session.execute(
            update(AIGatewayInvocationRow)
            .where(AIGatewayInvocationRow.idempotency_key == "repair-pre-record-crash")
            .values(execution_lease_expires_at=datetime(2026, 8, 9, tzinfo=UTC))
        )

    fresh_provider = DeterministicMockProvider("mock", prefix="Fresh")
    assert fresh_provider.calls == 0
    assert fresh_provider.effect_cache_size == 0
    restarted, _, _ = durable_ai_gateway(database, provider=fresh_provider)
    recovered = await restarted.invoke(replace(request, command_id="repair-pre-record-recovery"))
    assert recovered.result.result_status is ResultStatus.SUCCEEDED
    assert provider.calls == 2
    assert fresh_provider.calls == 0
    async with database.transaction() as session:
        repair_rows = list(
            await session.scalars(
                select(AIGatewayAttemptRow).where(
                    AIGatewayAttemptRow.ai_invocation_id == recovered.ai_invocation_id,
                    AIGatewayAttemptRow.effect_reference.like("%:repair:%"),
                )
            )
        )
        assert len(repair_rows) == 1
        assert repair_rows[0].state == "repair"
        assert repair_rows[0].result_payload is not None
        effects = list(
            await session.scalars(
                select(AIGatewayProviderEffectRow).where(
                    AIGatewayProviderEffectRow.ai_invocation_id == recovered.ai_invocation_id,
                    AIGatewayProviderEffectRow.effect_type == "structured_repair",
                )
            )
        )
        assert len(effects) == 1
        assert effects[0].state == "completed"
        assert effects[0].dispatch_count == 1


async def test_ai_gateway_structured_repair_effect_crash_reconciles_without_double_charge(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    class CrashAfterRepairEffect(PostgresAIGatewayStore):
        async def record_attempt(self, invocation_id: str, **values: Any) -> None:
            if int(values["attempt_number"]) >= 100:
                raise InjectedCrash
            await super().record_attempt(invocation_id, **values)

    provider = DeterministicMockProvider(
        "mock", prefix="Durable", behaviors=(MockProviderBehavior.MALFORMED,)
    )
    gateway, _, _ = durable_ai_gateway(database, provider=provider)
    gateway.store = CrashAfterRepairEffect(database)
    request = ai_request(
        idempotency_key="repair-post-effect-crash",
        response_mode=ResponseMode.STRUCTURED,
        output_schema_ref="answer-v1",
        max_total_cost=Decimal("0.05"),
    )
    with pytest.raises(InjectedCrash):
        await gateway.invoke(request)
    async with database.transaction() as session:
        await session.execute(
            update(AIGatewayInvocationRow)
            .where(AIGatewayInvocationRow.idempotency_key == "repair-post-effect-crash")
            .values(execution_lease_expires_at=datetime(2026, 8, 9, tzinfo=UTC))
        )

    fresh_provider = DeterministicMockProvider("mock", prefix="Fresh")
    assert fresh_provider.calls == 0
    assert fresh_provider.effect_cache_size == 0
    restarted, _, _ = durable_ai_gateway(database, provider=fresh_provider)
    recovered = await restarted.invoke(replace(request, command_id="repair-effect-recovery"))
    assert recovered.result.result_status is ResultStatus.SUCCEEDED
    assert provider.calls == 2
    assert fresh_provider.calls == 0
    async with database.transaction() as session:
        budget = await session.get(
            AIGatewayBudgetRow, ("tenant-1", "workspace-1", recovered.ai_invocation_id)
        )
        assert budget is not None
        assert budget.actual_amount is not None
        assert budget.actual_amount <= request.max_total_cost


async def test_ai_gateway_ambiguous_provider_effect_refuses_blind_replay(
    database: PostgresDatabase,
) -> None:
    class InjectedCrash(BaseException):
        pass

    request = ai_request(idempotency_key="repair-ambiguous-effect")
    gateway, _, _ = durable_ai_gateway(database)
    accepted = await gateway.accept(request)
    effect_key = f"{accepted.ai_invocation_id}:repair:101"
    process_a_calls = 0

    async def process_a_effect() -> ProviderResult:
        nonlocal process_a_calls
        process_a_calls += 1
        raise InjectedCrash

    with pytest.raises(InjectedCrash):
        await PostgresProviderEffectBoundary(database).execute(
            request=request,
            effect_key=effect_key,
            effect_type="structured_repair",
            request_hash="canonical-hash",
            operation=process_a_effect,
        )

    fresh_process_calls = 0

    async def fresh_process_effect() -> ProviderResult:
        nonlocal fresh_process_calls
        fresh_process_calls += 1
        return ProviderResult("{}", AIUsage(1, 1))

    with pytest.raises(ProviderFailure, match="AI_PROVIDER_EFFECT_AMBIGUOUS"):
        await PostgresProviderEffectBoundary(database).execute(
            request=request,
            effect_key=effect_key,
            effect_type="structured_repair",
            request_hash="canonical-hash",
            operation=fresh_process_effect,
        )
    assert process_a_calls == 1
    assert fresh_process_calls == 0
    async with database.transaction() as session:
        effect = await session.get(
            AIGatewayProviderEffectRow,
            (request.tenant_id, request.workspace_id, effect_key),
        )
        assert effect is not None
        assert effect.state == "dispatching"
        assert effect.dispatch_count == 1


async def test_ai_gateway_repair_reservation_expiry_releases_after_crash(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="repair-reservation-expiry"))
    await store.reserve(
        accepted.ai_invocation_id,
        "tenant-1",
        "workspace-1",
        Decimal("0.05"),
        now=datetime(2026, 8, 10, tzinfo=UTC),
        pricing_version="price-v1",
    )
    released = await PostgresAIGatewayStore(database).release_expired(
        now=datetime(2026, 8, 10, 0, 6, tzinfo=UTC)
    )
    assert released == (accepted.ai_invocation_id,)


async def test_ai_gateway_repair_and_fallback_cumulative_cap_survives_restart(
    database: PostgresDatabase,
) -> None:
    provider = DeterministicMockProvider(
        "mock",
        prefix="Durable",
        behaviors=(MockProviderBehavior.MALFORMED,),
    )
    gateway, _, _ = durable_ai_gateway(database, provider=provider)
    request = ai_request(
        idempotency_key="repair-cumulative-cap",
        response_mode=ResponseMode.STRUCTURED,
        output_schema_ref="answer-v1",
        max_total_cost=Decimal("0.05"),
    )
    response = await gateway.invoke(request)
    assert response.result.result_status is ResultStatus.SUCCEEDED
    replayed, _, _ = durable_ai_gateway(database, provider=provider)
    replay = await replayed.invoke(replace(request, command_id="repair-cap-replay"))
    assert replay.result.result_id == response.result.result_id
    async with database.transaction() as session:
        budget = await session.get(
            AIGatewayBudgetRow, ("tenant-1", "workspace-1", response.ai_invocation_id)
        )
        assert budget is not None and budget.actual_amount is not None
        assert budget.actual_amount <= request.max_total_cost


async def test_ai_gateway_partial_delayed_usage_expiry_and_no_double_charge(
    database: PostgresDatabase,
) -> None:
    gateway, _, store = durable_ai_gateway(database)
    accepted = await gateway.accept(ai_request(idempotency_key="usage-recovery"))
    await store.reserve(
        accepted.ai_invocation_id,
        "tenant-1",
        "workspace-1",
        Decimal("1"),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    usage = AIUsage(10, 5)
    await store.record_usage(
        accepted.ai_invocation_id,
        usage=usage,
        cost=Decimal("0.25"),
        event_key="provider-partial-1",
        kind="partial",
        final=False,
    )
    await store.record_usage(
        accepted.ai_invocation_id,
        usage=usage,
        cost=Decimal("0.25"),
        event_key="provider-partial-1",
        kind="partial",
        final=False,
    )
    assert await store.pending_reconciliation() == (accepted.ai_invocation_id,)
    restarted = PostgresAIGatewayStore(database)
    await restarted.record_usage(
        accepted.ai_invocation_id,
        usage=usage,
        cost=Decimal("0.10"),
        event_key="provider-delayed-final",
        kind="delayed",
        final=True,
    )
    async with database.transaction() as session:
        budget = await session.get(
            AIGatewayBudgetRow, ("tenant-1", "workspace-1", accepted.ai_invocation_id)
        )
        assert budget is not None
        assert budget.actual_amount == Decimal("0.35")
        assert budget.state == "committed"

    expiring = await gateway.accept(
        ai_request(idempotency_key="expiring", command_id="command-expiring")
    )
    await store.reserve(
        expiring.ai_invocation_id,
        "tenant-1",
        "workspace-1",
        Decimal("1"),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert expiring.ai_invocation_id in await restarted.release_expired(
        now=datetime(2026, 8, 10, 0, 6, tzinfo=UTC)
    )


def event(
    event_id: str, *, tenant: str = "tenant-1", workspace: str = "workspace-1"
) -> EventEnvelope:
    now = datetime.now(UTC)
    return EventEnvelope(
        event_id=event_id,
        event_type="ExecutionAttemptSucceeded",
        event_version="1.0",
        occurred_at=now,
        recorded_at=now,
        producer="Skill Runtime",
        correlation_id="correlation-1",
        subject="execution-1",
        tenant_id=tenant,
        workspace_id=workspace,
        payload={"result_id": "result-1"},
        metadata=EventMetadata(),
    )


@pytest.fixture
async def database() -> AsyncIterator[PostgresDatabase]:
    value = PostgresDatabase(database_url())
    await reset(value)
    yield value
    await value.close()


async def test_migration_revision_readiness_and_schema_parity(
    database: PostgresDatabase,
) -> None:
    assert await database.migration_readiness() == {
        "ready": True,
        "status": "compatible",
        "expected_revision": "20260811_0005",
        "deployed_revision": "20260811_0005",
    }
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(sync.dialect.get_table_names(sync)))
    assert tables == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES - {"alembic_version"}


async def test_readiness_rejects_missing_behind_and_diverged_revision(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as session:
        await session.execute(text("DROP TABLE alembic_version"))
    assert (await database.migration_readiness())["status"] == "version_table_missing"
    async with database.transaction() as session:
        await session.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        )
        await session.execute(text("INSERT INTO alembic_version VALUES ('20260726_9999')"))
    assert (await database.migration_readiness())["status"] == "behind_expected_head"
    async with database.transaction() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='diverged_revision'"))
    assert (await database.migration_readiness())["status"] == "ahead_or_diverged"
    async with database.transaction() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='20260811_0005'"))


async def test_explicit_downgrade_and_upgrade_from_empty_database(
    database: PostgresDatabase,
) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    await asyncio.to_thread(command.downgrade, config, "base")
    async with database.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(sync.dialect.get_table_names(sync)))
    assert not (EXPECTED_TABLES - {"alembic_version"}) & names
    await asyncio.to_thread(command.upgrade, config, "head")
    assert (await database.migration_readiness())["ready"] is True


async def test_atomic_state_and_outbox_commit_and_rollback(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(database)
    workflow = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "workflow_id": "workflow-atomic",
        "state": "Running",
        "version": 1,
        "correlation_id": "correlation-1",
        "payload": b"{}",
    }
    async with database.transaction() as session:
        await session.execute(insert(WorkflowRow).values(**workflow))
        await store.record_in_transaction(session, event("event-atomic"))
    async with database.transaction() as session:
        assert await session.get(WorkflowRow, ("tenant-1", "workspace-1", "workflow-atomic"))
        assert await session.get(OutboxEventRow, ("tenant-1", "workspace-1", "event-atomic"))
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await session.execute(
                insert(WorkflowRow).values(**(workflow | {"workflow_id": "workflow-rollback"}))
            )
            await store.record_in_transaction(session, event("event-rollback"))
            raise RuntimeError("rollback")
    async with database.transaction() as session:
        assert (
            await session.get(WorkflowRow, ("tenant-1", "workspace-1", "workflow-rollback")) is None
        )
        assert (
            await session.get(OutboxEventRow, ("tenant-1", "workspace-1", "event-rollback")) is None
        )


async def test_claim_locking_ack_failure_visibility_and_stale_reclamation(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(database)
    for index in range(4):
        await store.record(event(f"event-{index}"))
    claimed: list[set[str]] = []

    async def claim(owner: str) -> None:
        rows = await store.claim(owner=owner, batch_size=2, lease_seconds=30)
        claimed.append({item.event_id for item in rows})

    async with anyio.create_task_group() as group:
        group.start_soon(claim, "worker-a")
        group.start_soon(claim, "worker-b")
    assert claimed[0].isdisjoint(claimed[1])
    assert len(claimed[0] | claimed[1]) == 4
    first = next(iter(claimed[0]))
    assert await store.mark_delivered(
        first,
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        owner="worker-a",
    )
    second = next(iter(claimed[1]))
    assert await store.release_failed(
        second,
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        owner="worker-b",
        error="poison event",
        backoff_seconds=1,
    )
    later = datetime.now(UTC) + timedelta(seconds=31)
    reclaimed = await store.claim(owner="worker-c", batch_size=4, lease_seconds=30, now=later)
    assert first not in {item.event_id for item in reclaimed}
    async with database.transaction() as session:
        poison = await session.get(OutboxEventRow, ("tenant-1", "workspace-1", second))
        assert poison is not None and poison.last_error == "poison event"


async def test_cross_scope_constraints_and_repository_isolation(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as session:
        await session.execute(
            insert(WorkflowRow).values(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                workflow_id="shared-id",
                state="Running",
                version=1,
                correlation_id="correlation-a",
                payload=b"{}",
            )
        )
        await session.execute(
            insert(WorkflowRow).values(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                workflow_id="shared-id",
                state="Running",
                version=1,
                correlation_id="correlation-b",
                payload=b"{}",
            )
        )
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(
                insert(WorkflowStepRow).values(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    workflow_step_id="step-cross",
                    workflow_id="missing-in-scope",
                    state="Running",
                    attempt_number=0,
                    payload=b"{}",
                )
            )
    async with database.transaction() as session:
        rows = tuple(
            await session.scalars(
                select(WorkflowRow).where(
                    WorkflowRow.tenant_id == "tenant-a",
                    WorkflowRow.workspace_id == "workspace-a",
                )
            )
        )
    assert len(rows) == 1 and rows[0].correlation_id == "correlation-a"


async def test_composed_repositories_load_only_the_configured_scope(
    database: PostgresDatabase,
) -> None:
    shared_command_id = "command-shared-across-scopes"
    first_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    second_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-b",
        workspace_id="workspace-b",
    )
    first = compose(first_settings)
    second = compose(second_settings)
    first_result = await first.reference_runtime.run(
        "scope a",
        command_id=shared_command_id,
    )
    second_result = await second.reference_runtime.run(
        "scope b",
        command_id=shared_command_id,
    )
    await first.close()
    await second.close()

    first_restarted = compose(first_settings)
    second_restarted = compose(second_settings)
    first_repository = first_restarted.reference_runtime.workflow_repository
    second_repository = second_restarted.reference_runtime.workflow_repository
    assert isinstance(first_repository, PostgresWorkflowRepository)
    assert isinstance(second_repository, PostgresWorkflowRepository)
    await first_repository.prepare()
    await second_repository.prepare()
    assert set(first_repository.instances) == {first_result.subject_reference}
    assert set(second_repository.instances) == {second_result.subject_reference}
    assert first_result.subject_reference != second_result.subject_reference
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 2
    await first_restarted.close()
    await second_restarted.close()


async def test_postgres_composed_runtime_restart_idempotency_lineage_and_evidence(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    first = compose(settings)
    command_envelope = first.reference_runtime.build_request_command(
        "durable hello", command_id="command-durable", max_attempts=2
    )
    first_result = await first.reference_runtime.run_command(command_envelope)
    assert first_result.result_status is ResultStatus.SUCCEEDED
    workflow_id = first_result.subject_reference
    await first.close()

    restarted = compose(settings)
    second_result = await restarted.reference_runtime.run_command(command_envelope)
    assert second_result == first_result
    assert workflow_id in restarted.reference_runtime.workflow_repository.instances
    assert restarted.reference_runtime.decisions.contains(command_envelope.causation_id)
    async with restarted.database.transaction() as session:  # type: ignore[union-attr]
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)) == 3
        executions = tuple(
            await session.scalars(select(ExecutionRow).order_by(ExecutionRow.attempt_number))
        )
        assert len(executions) == 1
        assert executions[0].previous_execution_id is None
        assert (await session.scalar(select(func.count()).select_from(OutcomeRow)) or 0) >= 3
        assert (
            await session.scalar(select(func.count()).select_from(DecisionEvidenceRow)) or 0
        ) >= 1
        assert not tuple(
            await session.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type.like("%Command%"))
            )
        )
    await restarted.close()


async def test_concurrent_duplicate_submission_creates_one_workflow(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    left = compose(settings)
    right = compose(settings)
    command_envelope = left.reference_runtime.build_request_command(
        "concurrent hello", command_id="command-concurrent"
    )
    results: list[ResultEnvelope] = []

    async def run(root: CompositionRoot) -> None:
        result = await root.reference_runtime.run_command(command_envelope)
        results.append(result)

    async with anyio.create_task_group() as group:
        group.start_soon(run, left)
        group.start_soon(run, right)
    assert results[0] == results[1]
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    await left.close()
    await right.close()


async def test_scoped_idempotency_key_deduplicates_distinct_command_ids(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    left = compose(settings)
    right = compose(settings)
    first = left.reference_runtime.build_request_command(
        "same logical request",
        command_id="command-idempotency-left",
        idempotency_key="logical-request",
    )
    second = replace(
        first,
        command_id="command-idempotency-right",
        timestamp=first.timestamp + timedelta(microseconds=1),
    )
    results: list[ResultEnvelope] = []

    async def run(root: CompositionRoot, command_envelope: Any) -> None:
        results.append(await root.reference_runtime.run_command(command_envelope))

    async with anyio.create_task_group() as group:
        group.start_soon(run, left, first)
        group.start_soon(run, right, second)

    assert results[0] == results[1]
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        manager_receipts = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager"
                )
            )
        )
        assert len(manager_receipts) == 1
        assert manager_receipts[0].idempotency_key == "logical-request"
        assert manager_receipts[0].completed

    conflicting = replace(second, command_id="command-conflict", payload={"message": "changed"})
    with pytest.raises(ValueError, match="IdempotencyKey"):
        await right.reference_runtime.run_command(conflicting)
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
    await left.close()
    await right.close()


async def test_idempotency_scope_replay_rollback_and_retry_lineage_matrix(
    database: PostgresDatabase,
) -> None:
    shared_key = "matrix-logical-request"
    first_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        mock_ai_failures_before_success=1,
    )
    other_scope_settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        tenant_id="tenant-b",
        workspace_id="workspace-b",
    )
    first = compose(first_settings)
    original = first.reference_runtime.build_request_command(
        "retry lineage",
        command_id="matrix-command-a",
        idempotency_key=shared_key,
        max_attempts=2,
    )
    result = await first.reference_runtime.run_command(original)
    assert result.result_status is ResultStatus.SUCCEEDED
    replay_command = replace(
        original,
        command_id="matrix-command-b",
        timestamp=original.timestamp + timedelta(seconds=1),
    )
    replay = await first.reference_runtime.run_command(replay_command)
    assert replay == result

    other = compose(other_scope_settings)
    independent = other.reference_runtime.build_request_command(
        "independent scope",
        command_id="matrix-command-c",
        idempotency_key=shared_key,
    )
    other_result = await other.reference_runtime.run_command(independent)
    assert other_result.subject_reference != result.subject_reference

    async with database.transaction() as session:
        workflows = tuple(
            await session.scalars(select(WorkflowRow).order_by(WorkflowRow.tenant_id))
        )
        assert len(workflows) == 2
        first_executions = tuple(
            await session.scalars(
                select(ExecutionRow)
                .where(
                    ExecutionRow.tenant_id == "tenant-a",
                    ExecutionRow.workspace_id == "workspace-a",
                )
                .order_by(ExecutionRow.attempt_number)
            )
        )
        assert len(first_executions) == 2
        assert first_executions[0].execution_id != first_executions[1].execution_id
        assert first_executions[1].previous_execution_id == first_executions[0].execution_id
        manager_claims = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager",
                    CommandIdempotencyRow.idempotency_key == shared_key,
                )
            )
        )
        assert len(manager_claims) == 2
        assert {row.tenant_id for row in manager_claims} == {"tenant-a", "tenant-b"}
        before = {
            "workflows": len(workflows),
            "executions": await session.scalar(select(func.count()).select_from(ExecutionRow)),
            "outcomes": await session.scalar(select(func.count()).select_from(OutcomeRow)),
            "events": await session.scalar(select(func.count()).select_from(OutboxEventRow)),
            "claims": await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)),
        }
    assert await first.reference_runtime.run_command(replay_command) == result
    async with database.transaction() as session:
        after = {
            "workflows": await session.scalar(select(func.count()).select_from(WorkflowRow)),
            "executions": await session.scalar(select(func.count()).select_from(ExecutionRow)),
            "outcomes": await session.scalar(select(func.count()).select_from(OutcomeRow)),
            "events": await session.scalar(select(func.count()).select_from(OutboxEventRow)),
            "claims": await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)),
        }
    assert after == before
    await first.close()
    await other.close()


async def test_idempotency_claim_and_workflow_staging_roll_back_atomically(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    participant = runtime.durable_participants[-1]
    original_flush = participant.flush_in_transaction
    injected = False

    async def fail_before_commit(session: Any) -> None:
        nonlocal injected
        await original_flush(session)
        if not injected:
            injected = True
            raise RuntimeError("injected failure before checkpoint commit")

    participant.flush_in_transaction = fail_before_commit  # type: ignore[method-assign]
    original = runtime.build_request_command(
        "rollback logical request",
        command_id="rollback-command-a",
        idempotency_key="rollback-key",
    )
    with pytest.raises(RuntimeError, match="checkpoint commit"):
        await runtime.run_command(original)
    assert injected
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 0
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
        assert await session.scalar(select(func.count()).select_from(OutcomeRow)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxEventRow)) == 0
        assert await session.scalar(select(func.count()).select_from(CommandIdempotencyRow)) == 0
    await interrupted.close()

    recovered = compose(settings)
    retry = replace(
        original,
        command_id="rollback-command-b",
        timestamp=original.timestamp + timedelta(seconds=1),
    )
    result = await recovered.reference_runtime.run_command(retry)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CommandIdempotencyRow)
                .where(
                    CommandIdempotencyRow.target_component == "Manager",
                    CommandIdempotencyRow.completed.is_(True),
                )
            )
            == 1
        )
    await recovered.close()


async def test_idempotency_scope_columns_and_locks_are_independent(
    database: PostgresDatabase,
) -> None:
    scope_rows = (
        ("tenant-a", "workspace-a", "Manager"),
        ("tenant-b", "workspace-a", "Manager"),
        ("tenant-a", "workspace-b", "Manager"),
        ("tenant-a", "workspace-a", "Workflow Engine"),
    )
    async with database.transaction() as session:
        for tenant_id, workspace_id, target in scope_rows:
            await session.execute(
                insert(CommandIdempotencyRow).values(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    target_component=target,
                    idempotency_key="same-key",
                    command_id="same-command-id",
                    command_hash="a" * 64,
                    completed=False,
                    payload=b"scope-proof",
                )
            )
    async with database.transaction() as session:
        rows = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.idempotency_key == "same-key"
                )
            )
        )
        assert {(row.tenant_id, row.workspace_id, row.target_component) for row in rows} == set(
            scope_rows
        )

    entered: set[str] = set()
    both_entered = anyio.Event()
    release = anyio.Event()

    async def hold(name: str, scope: str) -> None:
        async with database.command_lock(scope):
            entered.add(name)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()

    async with anyio.create_task_group() as group:
        group.start_soon(hold, "tenant", "tenant-a\x1fworkspace-a\x1fManager\x1fsame-key")
        group.start_soon(hold, "workspace", "tenant-a\x1fworkspace-b\x1fManager\x1fsame-key")
        with anyio.fail_after(2):
            await both_entered.wait()
        release.set()


async def test_per_consumer_receipts_are_independent_and_recover_stale_claims(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("first", "second")},
    )
    bus = InProcessEventBus()

    class Recorder:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def consume(self, event: EventEnvelope) -> None:
            self.events.append(event.event_id)

    first = Recorder()
    second = Recorder()
    bus.subscribe("ExecutionAttemptSucceeded", "first", first)
    bus.subscribe("ExecutionAttemptSucceeded", "second", second)
    relay = PostgresOutboxRelay(
        store,
        bus,
        owner="relay-a",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    delivered_event = event("event-receipts")
    await store.record(delivered_event)
    assert await relay.drain() == 1
    assert first.events == ["event-receipts"]
    assert second.events == ["event-receipts"]
    async with database.transaction() as session:
        receipts = tuple(
            await session.scalars(
                select(DeliveryReceiptRow).order_by(DeliveryReceiptRow.consumer_name)
            )
        )
        assert [(item.consumer_name, item.status) for item in receipts] == [
            ("first", "Delivered"),
            ("second", "Delivered"),
        ]

    stale_event = event("event-stale-receipt")
    await store.record(stale_event)
    instant = datetime.now(UTC)
    assert await store.claim_receipt(
        stale_event,
        "first",
        owner="dead-worker",
        lease_seconds=1,
        now=instant,
    )
    assert not await store.claim_receipt(
        stale_event,
        "first",
        owner="competing-worker",
        lease_seconds=1,
        now=instant,
    )
    assert await store.claim_receipt(
        stale_event,
        "first",
        owner="recovery-worker",
        lease_seconds=30,
        now=instant + timedelta(seconds=2),
    )


async def test_required_consumer_snapshot_survives_missing_and_changed_registration(
    database: PostgresDatabase,
) -> None:
    stored_event = event("event-membership-snapshot")
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("first", "second")},
    )
    await store.record(stored_event)
    empty_bus = InProcessEventBus()
    empty_relay = PostgresOutboxRelay(
        store,
        empty_bus,
        owner="empty-runtime",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await empty_relay.drain() == 0
    async with database.transaction() as session:
        outbox = await session.get(
            OutboxEventRow, ("tenant-1", "workspace-1", stored_event.event_id)
        )
        assert outbox is not None
        assert outbox.required_consumer_count == 2
        assert outbox.delivered_at is None
        receipts = tuple(await session.scalars(select(DeliveryReceiptRow)))
        assert {receipt.consumer_name for receipt in receipts} == {"first", "second"}
        assert all(receipt.status == "Failed" for receipt in receipts)

    class Recorder:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def consume(self, event: EventEnvelope) -> None:
            self.events.append(event.event_id)

    restarted_bus = InProcessEventBus()
    first = Recorder()
    second = Recorder()
    optional = Recorder()
    restarted_bus.subscribe(stored_event.event_type, "first", first)
    restarted_bus.subscribe(stored_event.event_type, "second", second)
    restarted_bus.subscribe(stored_event.event_type, "later-optional", optional)
    restarted_store = PostgresOutboxStore(database, required_consumers={})
    restarted_relay = PostgresOutboxRelay(
        restarted_store,
        restarted_bus,
        owner="complete-runtime",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await restarted_relay.drain() == 1
    assert first.events == [stored_event.event_id]
    assert second.events == [stored_event.event_id]
    assert optional.events == []


async def test_receipt_contention_failure_poison_and_scope_safety_matrix(
    database: PostgresDatabase,
) -> None:
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("healthy", "poison")},
    )
    stored_event = event("event-receipt-matrix")
    await store.record(stored_event)
    instant = datetime.now(UTC)
    claims: list[bool] = []

    async def contend(owner: str) -> None:
        claims.append(
            await store.claim_receipt(
                stored_event,
                "healthy",
                owner=owner,
                lease_seconds=30,
                now=instant,
            )
        )

    async with anyio.create_task_group() as group:
        group.start_soon(contend, "worker-a")
        group.start_soon(contend, "worker-b")
    assert sorted(claims) == [False, True]
    assert await store.claim_receipt(
        stored_event,
        "poison",
        owner="worker-independent",
        lease_seconds=30,
        now=instant,
    )
    assert await store.fail_receipt(
        stored_event,
        "poison",
        owner="worker-independent",
        error="safe-poison-category",
    )
    async with database.transaction() as session:
        poison = await session.get(
            DeliveryReceiptRow,
            ("tenant-1", "workspace-1", stored_event.event_id, "poison"),
        )
        assert poison is not None
        assert poison.status == "Failed"
        assert poison.delivery_attempts == 1
        assert poison.last_error == "safe-poison-category"
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await session.execute(
                insert(DeliveryReceiptRow).values(
                    tenant_id="other-tenant",
                    workspace_id="workspace-1",
                    event_id=stored_event.event_id,
                    consumer_name="cross-scope",
                    status="Pending",
                    required=True,
                    delivery_attempts=0,
                    created_at=instant,
                )
            )


async def test_partial_acknowledgement_and_poison_retry_preserve_healthy_effect(
    database: PostgresDatabase,
) -> None:
    stored_event = event("event-partial-poison")
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("healthy", "poison")},
    )
    await store.record(stored_event)

    class Healthy:
        calls = 0

        async def consume(self, event: EventEnvelope) -> None:
            assert event.event_id == stored_event.event_id
            self.calls += 1

    class PoisonOnce:
        calls = 0
        effects = 0

        async def consume(self, event: EventEnvelope) -> None:
            assert event.event_id == stored_event.event_id
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("poison")
            self.effects += 1

    healthy = Healthy()
    poison = PoisonOnce()
    bus = InProcessEventBus()
    bus.subscribe(stored_event.event_type, "healthy", healthy)
    bus.subscribe(stored_event.event_type, "poison", poison)
    relay = PostgresOutboxRelay(
        store,
        bus,
        owner="poison-worker",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await relay.drain() == 0
    async with database.transaction() as session:
        receipts = {
            receipt.consumer_name: receipt
            for receipt in await session.scalars(select(DeliveryReceiptRow))
        }
        outbox = await session.get(
            OutboxEventRow, ("tenant-1", "workspace-1", stored_event.event_id)
        )
        assert receipts["healthy"].status == "Delivered"
        assert receipts["healthy"].delivery_attempts == 1
        assert receipts["poison"].status == "Failed"
        assert receipts["poison"].last_error == "RuntimeError"
        assert outbox is not None and outbox.delivered_at is None
    assert healthy.calls == 1
    assert poison.calls == 1
    assert poison.effects == 0

    assert await relay.drain() == 1
    async with database.transaction() as session:
        receipts = {
            receipt.consumer_name: receipt
            for receipt in await session.scalars(select(DeliveryReceiptRow))
        }
        outbox = await session.get(
            OutboxEventRow, ("tenant-1", "workspace-1", stored_event.event_id)
        )
        assert receipts["healthy"].status == "Delivered"
        assert receipts["healthy"].delivery_attempts == 1
        assert receipts["poison"].status == "Delivered"
        assert receipts["poison"].delivery_attempts == 2
        assert outbox is not None and outbox.delivered_at is not None
    assert healthy.calls == 1
    assert poison.calls == 2
    assert poison.effects == 1


async def test_crash_after_consumer_effect_has_one_durable_authoritative_effect(
    database: PostgresDatabase,
) -> None:
    stored_event = event("event-crash-after-effect")
    store = PostgresOutboxStore(
        database,
        required_consumers={"ExecutionAttemptSucceeded": ("durable-consumer",)},
    )
    await store.record(stored_event)

    class DurableConsumer:
        def __init__(self, *, crash: bool) -> None:
            self.crash = crash

        async def consume(self, event: EventEnvelope) -> None:
            async with database.transaction() as session:
                await session.execute(
                    postgres_insert(DecisionEvidenceRow)
                    .values(
                        tenant_id=event.tenant_id,
                        workspace_id=event.workspace_id,
                        decision_id=f"consumer-effect:{event.event_id}",
                        decision_type="ConsumerAuthoritativeEffect",
                        component="Test Durable Consumer",
                        correlation_id=event.correlation_id,
                        triggering_id=event.event_id,
                        recorded_at=event.recorded_at,
                        payload=b"authoritative-effect",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "workspace_id", "decision_id"]
                    )
                )
            if self.crash:
                raise RuntimeError("crash after effect")

    crashing_bus = InProcessEventBus()
    crashing_bus.subscribe(stored_event.event_type, "durable-consumer", DurableConsumer(crash=True))
    crashing_relay = PostgresOutboxRelay(
        store,
        crashing_bus,
        owner="crashing-worker",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await crashing_relay.drain() == 0

    recovered_bus = InProcessEventBus()
    recovered_bus.subscribe(
        stored_event.event_type,
        "durable-consumer",
        DurableConsumer(crash=False),
    )
    recovered_relay = PostgresOutboxRelay(
        PostgresOutboxStore(database),
        recovered_bus,
        owner="recovered-worker",
        batch_size=10,
        lease_seconds=30,
        backoff_seconds=0,
    )
    assert await recovered_relay.drain() == 1
    async with database.transaction() as session:
        effects = tuple(
            await session.scalars(
                select(DecisionEvidenceRow).where(
                    DecisionEvidenceRow.decision_type == "ConsumerAuthoritativeEffect"
                )
            )
        )
        receipt = await session.get(
            DeliveryReceiptRow,
            ("tenant-1", "workspace-1", stored_event.event_id, "durable-consumer"),
        )
        assert len(effects) == 1
        assert receipt is not None and receipt.status == "Delivered"
        assert receipt.delivery_attempts == 2


async def test_retry_creates_new_execution_and_durable_decision_lineage(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        mock_ai_failures_before_success=1,
    )
    root = compose(settings)
    result = await root.reference_runtime.run("retry hello", max_attempts=2)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        executions = tuple(
            await session.scalars(select(ExecutionRow).order_by(ExecutionRow.attempt_number))
        )
        assert len(executions) == 2
        assert executions[0].execution_id != executions[1].execution_id
        assert executions[1].previous_execution_id == executions[0].execution_id
        decisions = tuple(await session.scalars(select(DecisionEvidenceRow)))
        assert any(item.decision_type == "RetryExecutionAttempt" for item in decisions)
        terminal = tuple(
            await session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type.in_(("WorkflowCompleted", "WorkflowFailed"))
                )
            )
        )
        assert len(terminal) == 1
    await root.close()


async def test_failed_execution_persists_error_and_terminal_result(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        mock_ai_failures_before_success=2,
    )
    root = compose(settings)
    result = await root.reference_runtime.run("fail durably", max_attempts=1)
    assert result.result_status is ResultStatus.FAILED
    assert result.error_id is not None
    async with database.transaction() as session:
        error = await session.get(
            OutcomeRow,
            (settings.tenant_id, settings.workspace_id, result.error_id),
        )
        terminal = await session.get(
            OutcomeRow,
            (settings.tenant_id, settings.workspace_id, result.result_id),
        )
        assert error is not None and error.kind == "Error"
        assert terminal is not None and terminal.kind == "Result" and terminal.terminal
    await root.close()


async def test_readiness_reports_unreachable_database_without_leaking_credentials() -> None:
    database = PostgresDatabase(
        "postgresql+asyncpg://user:secret@127.0.0.1:1/unreachable",  # pragma: allowlist secret
        pool_timeout_seconds=0.1,
        command_timeout_seconds=0.1,
    )
    try:
        status = await database.migration_readiness()
        assert status == {
            "ready": False,
            "status": "database_unreachable",
            "expected_revision": "20260811_0005",
        }
        assert "secret" not in repr(status)
    finally:
        await database.close()


async def test_incomplete_publication_and_idempotency_resume_after_restart(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
        delivery_backoff_seconds=0.01,
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    consumers = cast(
        dict[str, list[tuple[str, Any]]],
        vars(runtime.event_bus)["_consumers"],
    )
    original = consumers["ExecutionAttemptSucceeded"][0][1]

    class FailOnce:
        failed = False

        async def consume(self, delivered: EventEnvelope) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected process interruption")
            await original.consume(delivered)

    consumers["ExecutionAttemptSucceeded"] = [("workflow-engine", FailOnce())]
    command_envelope = runtime.build_request_command("resume hello", command_id="command-resume")
    acknowledgement = await runtime.run_command(command_envelope)
    assert acknowledgement.result_status is ResultStatus.ACCEPTED
    conflicting = replace(
        command_envelope,
        command_id="command-resume-conflict",
        payload={"message": "changed while incomplete"},
    )
    with pytest.raises(ValueError, match="IdempotencyKey"):
        await runtime.run_command(conflicting)
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    await interrupted.close()

    await anyio.sleep(0.02)
    recovered = compose(settings)
    replay_command = replace(
        command_envelope,
        command_id="command-resume-redelivery",
        timestamp=command_envelope.timestamp + timedelta(seconds=1),
    )
    result = await recovered.reference_runtime.run_command(replay_command)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
        terminal = tuple(
            await session.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type == "WorkflowCompleted")
            )
        )
        assert len(terminal) == 1
        manager_claims = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Manager"
                )
            )
        )
        assert len(manager_claims) == 1
        assert manager_claims[0].command_id == command_envelope.command_id
        assert manager_claims[0].completed
    await recovered.close()


async def test_memory_terminal_checkpoint_rolls_back_and_redelivery_is_exactly_once(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    interrupted = compose(settings)
    runtime = interrupted.reference_runtime
    execution_repository = cast(Any, runtime.execution_repository)
    memory_repository = cast(Any, runtime.memory_repository)
    original_flush = execution_repository.flush_in_transaction
    injected = False

    async def fail_after_staged_memory(session: Any) -> None:
        nonlocal injected
        if not injected and memory_repository._pending:
            injected = True
            raise RuntimeError("injected failure after Memory staging")
        await original_flush(session)

    execution_repository.flush_in_transaction = fail_after_staged_memory
    command_envelope = runtime.build_request_command(
        "atomic memory hello",
        command_id="command-memory-atomicity",
    )

    with pytest.raises(RuntimeError, match="after Memory staging"):
        await runtime.run_command(command_envelope)
    assert injected

    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryRecordRow)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutcomeRow)
                .where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.terminal.is_(True),
                )
            )
            == 0
        )
        execution = await session.scalar(select(ExecutionRow))
        assert execution is not None and execution.state == "Executing"
        skill_receipt = await session.scalar(
            select(CommandIdempotencyRow).where(
                CommandIdempotencyRow.target_component == "Skill Runtime"
            )
        )
        assert skill_receipt is not None and not skill_receipt.completed
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.event_type.in_(
                        (
                            "ExecutionAttemptSucceeded",
                            "ExecutionAttemptFailed",
                            "ExecutionAttemptTimedOut",
                        )
                    )
                )
            )
            == 0
        )
    await interrupted.close()

    recovered = compose(settings)
    result = await recovered.reference_runtime.run_command(command_envelope)
    assert result.result_status is ResultStatus.SUCCEEDED
    async with database.transaction() as session:
        memories = tuple(await session.scalars(select(MemoryRecordRow)))
        assert len(memories) == 1
        execution = await session.scalar(select(ExecutionRow))
        assert execution is not None and execution.state == "Succeeded"
        terminal_outcomes = tuple(
            await session.scalars(
                select(OutcomeRow).where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.subject_id == execution.execution_id,
                    OutcomeRow.terminal.is_(True),
                )
            )
        )
        assert len(terminal_outcomes) == 1
        completed_receipts = tuple(
            await session.scalars(
                select(CommandIdempotencyRow).where(
                    CommandIdempotencyRow.target_component == "Skill Runtime",
                    CommandIdempotencyRow.completed.is_(True),
                )
            )
        )
        assert len(completed_receipts) == 1
        terminal_events = tuple(
            await session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "ExecutionAttemptSucceeded"
                )
            )
        )
        assert len(terminal_events) == 1
    replay = await recovered.reference_runtime.run_command(command_envelope)
    assert replay == result
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryRecordRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutcomeRow)
                .where(
                    OutcomeRow.owner_component == "Skill Runtime",
                    OutcomeRow.terminal.is_(True),
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "ExecutionAttemptSucceeded")
            )
            == 1
        )
    await recovered.close()


async def test_authoritative_result_v2_survives_restart_and_duplicate_delivery_zero_call(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    first = compose(settings)
    runtime = first.reference_runtime
    await runtime.run("seed durable workflow parents")
    workflow = next(iter(runtime.workflow_repository.instances.values()))
    runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    base = CommandEnvelope(
        command_id="command-authoritative-source",
        command_type="DispatchExecutionAttempt",
        command_version="1.0",
        correlation_id="correlation-authoritative-source",
        causation_id="workflow-authoritative-source",
        target_component="Skill Runtime",
        initiator="Workflow Engine",
        timestamp=datetime(2026, 8, 15, tzinfo=UTC),
        tenant_id=settings.tenant_id,
        workspace_id=settings.workspace_id,
        workflow_id=workflow.workflow_id,
        workflow_step_id=workflow.workflow_step_id,
        execution_id="execution-authoritative-source",
        payload={
            "skill_version_id": "structured-task-kind-skill-v1",
            "statement": "What is the status?",
        },
        metadata=CommandMetadata(
            request_id="request-authoritative-source",
            idempotency_key="idem-authoritative-source",
            attempt_number=2,
            authorization=runtime.authorization,
        ),
    )
    await runtime.run_execution_command(base)
    source = runtime.execution_repository.records["execution-authoritative-source"].result
    assert source is not None
    await first.close()

    recovered = compose(settings)
    recovered_runtime = recovered.reference_runtime
    recovered_runtime.event_bus._consumers.clear()  # pyright: ignore[reportPrivateUsage]
    reuse = replace(
        base,
        command_id="command-authoritative-reuse",
        command_version="2",
        correlation_id="correlation-authoritative-reuse",
        causation_id="workflow-authoritative-reuse",
        workflow_id=workflow.workflow_id,
        workflow_step_id=workflow.workflow_step_id,
        execution_id="execution-authoritative-reuse",
        payload={"statement": "What is the status?"},
        metadata=CommandMetadata(
            request_id="request-authoritative-reuse",
            idempotency_key="idem-authoritative-reuse",
            attempt_number=3,
            authorization=recovered_runtime.authorization,
            skill_version_id="structured-task-kind-skill-v1",
            authoritative_result_id=source.result_id,
        ),
    )
    first_delivery, duplicate = await asyncio.gather(
        recovered_runtime.run_execution_command(reuse),
        recovered_runtime.run_execution_command(reuse),
    )
    assert first_delivery == duplicate
    result = recovered_runtime.execution_repository.records["execution-authoritative-reuse"].result
    assert result is not None and result.metadata["reused_result_id"] == source.result_id
    assert result.metadata["ai_invocation_id"] == ""
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(AIGatewayInvocationRow)) == 1
    await recovered.close()


async def test_structured_cancellation_uses_governed_event_and_workflow_is_terminal_after_restart(
    database: PostgresDatabase,
) -> None:
    settings = HostSettings(
        runtime_adapter=RuntimeAdapter.POSTGRES,
        database_url=SecretStr(database_url()),
    )
    first = compose(settings)
    runtime = first.reference_runtime
    adapter = runtime.reference_ai_gateway._adapters[  # pyright: ignore[reportPrivateUsage]
        "mock-economy"
    ]
    assert isinstance(adapter, DeterministicMockProvider)
    adapter._behaviors = [MockProviderBehavior.CANCELLED]  # pyright: ignore[reportPrivateUsage]
    command_envelope = CommandEnvelope(
        command_id="command-structured-cancellation",
        command_type="StartWorkflow",
        command_version="1.0",
        correlation_id="correlation-structured-cancellation",
        causation_id="decision-structured-cancellation",
        target_component="Workflow Engine",
        initiator="Manager",
        timestamp=datetime(2026, 8, 15, tzinfo=UTC),
        tenant_id=settings.tenant_id,
        workspace_id=settings.workspace_id,
        payload={
            "workflow_definition_id": "structured-cancellation-workflow",
            "workflow_definition_version_id": "v1",
            "skill_version_id": "structured-task-kind-skill-v1",
            "max_attempts": 1,
            "statement": "What is the status?",
            "timeout_seconds": 5,
        },
        metadata=CommandMetadata(
            request_id="request-structured-cancellation",
            idempotency_key="idem-structured-cancellation",
            authorization=runtime.authorization,
        ),
    )
    await runtime.run_workflow_command(command_envelope)
    workflow = next(iter(runtime.workflow_repository.instances.values()))
    execution = next(iter(runtime.execution_repository.records.values()))
    assert execution.result is not None
    assert execution.result.result_status is ResultStatus.FAILED
    assert execution.terminal_event is not None
    assert execution.terminal_event.event_type == "ExecutionAttemptFailed"
    assert workflow.outcome is not None and workflow.outcome.result_status is ResultStatus.FAILED
    workflow_id = workflow.workflow_id
    await first.close()

    recovered = compose(settings)
    for participant in recovered.reference_runtime.durable_participants:
        await participant.prepare()
    durable_workflow = recovered.reference_runtime.workflow_repository.instances[workflow_id]
    assert durable_workflow.outcome is not None
    assert durable_workflow.outcome.result_status is ResultStatus.FAILED
    async with database.transaction() as session:
        cancelled_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(OutboxEventRow.event_type == "ExecutionAttemptCancelled")
        )
        assert cancelled_events == 0
    await recovered.close()
