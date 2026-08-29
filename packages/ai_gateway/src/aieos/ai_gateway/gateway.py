"""Provider-neutral AI Gateway contracts and deterministic reference runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, cast
from uuid import uuid4

from aieos.ai_gateway.prompt_pipeline import PromptPackageCatalog
from aieos.contracts import (
    AuthorizationContext,
    DataClassification,
    ErrorCategory,
    ErrorEnvelope,
    ErrorSeverity,
    LogSeverity,
    ObservabilityContext,
    OutcomeCategory,
    RedactionStatus,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.domain import Clock, IdentifierFactory
from aieos.observability import ObservationRecorder
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import ScopeAuthorizer


class ResponseMode(StrEnum):
    TEXT = "text"
    STRUCTURED = "structured"
    STREAM = "stream"


class InvocationState(StrEnum):
    REQUESTED = "Requested"
    POLICY_VALIDATED = "PolicyValidated"
    PROVIDER_SELECTED = "ProviderSelected"
    PREPARED = "Prepared"
    INVOKED = "Invoked"
    STREAMING = "Streaming"
    RETRYING = "Retrying"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"


@dataclass(frozen=True, slots=True)
class ContextItem:
    reference: str
    version: str
    content: str
    relevance: int
    necessity_reason: str
    trusted: bool = False
    mandatory: bool = False


@dataclass(frozen=True, slots=True)
class AIInvocationRequest:
    execution_id: str
    capability_contract_version_id: str
    prompt: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    causation_id: str
    authorization: AuthorizationContext
    command_id: str
    idempotency_key: str
    capability_id: str = "text-generation"
    prompt_template_ref: str = "reference-template"
    prompt_template_version_ref: str = "v1"
    system_instruction_ref: str = "reference-system-v1"
    response_mode: ResponseMode = ResponseMode.TEXT
    output_schema_ref: str | None = None
    # A governed structured request carries the immutable package-resolved schema
    # rather than asking the Gateway or a provider adapter to look up/redefine it.
    # It is intentionally provider-neutral JSON Schema material.
    output_schema: Mapping[str, object] | None = None
    output_schema_identity: str | None = None
    required_capabilities: frozenset[str] = frozenset({"text"})
    quality_tier: int = 1
    latency_tier: int = 1
    max_input_tokens: int = 1024
    max_output_tokens: int = 128
    max_total_cost: Decimal = Decimal("1.00")
    deadline: datetime | None = None
    context_items: tuple[ContextItem, ...] = ()
    locale: str = "en"
    data_classification: DataClassification = DataClassification.INTERNAL
    safety_policy_ref: str = "reference-safety-v1"
    cache_policy_ref: str = "reference-cache-v1"
    budget_policy_ref: str = "reference-budget-v1"
    allowed_adapters: frozenset[str] = frozenset()
    blocked_adapters: frozenset[str] = frozenset()
    residency: str = "any"
    required_data_handling: frozenset[str] = frozenset()
    minimum_security_tier: int = 1
    allow_fallback: bool = True
    max_provider_attempts: int = 2
    repair_attempts: int = 1
    cache_allowed: bool = True
    deterministic_parameters: tuple[tuple[str, str], ...] = ()
    workflow_ai_budget_admission: Mapping[str, object] | None = None
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    workflow_definition_version_id: str | None = None
    skill_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class AIUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecision:
    decision_reference: str
    model_key: str
    adapter_key: str
    considered: tuple[str, ...]
    excluded: Mapping[str, str]
    estimated_cost: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class AIInvocationResponse:
    ai_invocation_id: str
    result: ResultEnvelope
    content: str | None = None
    error: ErrorEnvelope | None = None
    usage: AIUsage | None = None
    route: RouteDecision | None = None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class Acceptance:
    ai_invocation_id: str
    acknowledgement: ResultEnvelope
    replay: bool = False


@dataclass(frozen=True, slots=True)
class StreamChunk:
    ai_invocation_id: str
    sequence: int
    kind: str
    content: str | None = None
    usage: AIUsage | None = None
    terminal: AIInvocationResponse | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    model_key: str
    adapter_key: str
    capabilities: frozenset[str]
    context_limit: int
    max_output: int
    quality_tier: int
    latency_tier: int
    input_cost_per_token: Decimal
    output_cost_per_token: Decimal
    pricing_version: str
    residencies: frozenset[str] = frozenset({"any"})
    data_handling: frozenset[str] = frozenset({"internal"})
    security_tier: int = 1
    available: bool = True
    healthy: bool = True
    deprecated: bool = False
    cached_input_cost_per_token: Decimal | None = None
    reasoning_cost_per_token: Decimal | None = None

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> Decimal:
        cached = min(input_tokens, max(0, cached_tokens))
        uncached = input_tokens - cached
        cached_rate = self.cached_input_cost_per_token or self.input_cost_per_token
        reasoning_rate = self.reasoning_cost_per_token or self.output_cost_per_token
        return (
            uncached * self.input_cost_per_token
            + cached * cached_rate
            + output_tokens * self.output_cost_per_token
            + reasoning_tokens * reasoning_rate
        )


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    usage: AIUsage | None
    confidence: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    """Provider-neutral incremental event; adapter-native chunks never escape the adapter."""

    kind: str
    content: str | None = None
    usage: AIUsage | None = None


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, usage: AIUsage | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.usage = usage


class ExecutionOwnershipLost(RuntimeError):
    """The durable fencing generation no longer belongs to this worker."""


class ProviderAdapter(Protocol):
    key: str

    async def invoke(
        self,
        *,
        model_key: str,
        prompt: str,
        request: AIInvocationRequest,
        effect_key: str | None = None,
    ) -> ProviderResult: ...

    def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[ProviderStreamEvent]: ...


class ProviderEffectBoundary(Protocol):
    """Process-independent idempotency boundary owned by a provider adapter."""

    async def execute(
        self,
        *,
        request: AIInvocationRequest,
        effect_key: str,
        effect_type: str,
        request_hash: str,
        operation: Callable[[], Awaitable[ProviderResult]],
    ) -> ProviderResult: ...


class AIGateway(Protocol):
    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse: ...


class WorkflowAdmissionAuthority(Protocol):
    """Resolve the current Workflow-owned durable admission at the Gateway boundary."""

    async def authoritative_ai_admission(
        self,
        *,
        workflow_id: str,
        command_id: str,
        execution_id: str,
    ) -> Mapping[str, object] | None: ...


@dataclass(slots=True)
class GatewayInvocation:
    request: AIInvocationRequest
    invocation_id: str
    acknowledgement: ResultEnvelope
    state: InvocationState
    accepted_at: datetime
    route: RouteDecision | None = None
    terminal: AIInvocationResponse | None = None
    execution_owner: str | None = None
    execution_lease_expires_at: datetime | None = None
    claim_generation: int = 0
    recovery_phase: str = "accepted"
    terminal_intent: AIInvocationResponse | None = None
    stream_started: bool = False
    stream_sequence: int = 0
    stream_content: tuple[str, ...] = ()
    stream_usage: AIUsage | None = None
    provider_sequence: tuple[RouteDecision, ...] = ()
    next_provider_attempt: int = 1
    cumulative_usage: AIUsage = AIUsage(0, 0)
    cumulative_cost: Decimal = Decimal("0")
    last_provider_failure_code: str | None = None
    last_provider_failure_retryable: bool = False
    stream_terminal_emitted: bool = False


@dataclass(slots=True)
class GatewayReservation:
    invocation_id: str
    tenant_id: str
    workspace_id: str
    amount: Decimal
    state: str
    expires_at: datetime
    actual: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CachedContent:
    content: str
    usage: AIUsage
    expires_at: datetime
    provenance: str
    route: RouteDecision | None = None


class ReferenceGatewayStore:
    """Atomic in-memory implementation of the Gateway persistence port."""

    def __init__(
        self,
        *,
        tenant_limit: Decimal = Decimal("100"),
        workspace_limit: Decimal | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self.invocations: dict[str, GatewayInvocation] = {}
        self.admission: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.reservations: dict[str, GatewayReservation] = {}
        self.cache: dict[tuple[str, str, str], CachedContent] = {}
        self.usage: dict[str, AIUsage] = {}
        self.attempts: dict[str, list[tuple[int, str, str, AIUsage | None, Decimal]]] = {}
        self._usage_events: dict[str, dict[str, tuple[AIUsage, Decimal]]] = {}
        self._provider_effects: dict[tuple[str, str], ProviderResult] = {}
        self._provider_effect_reservations: set[tuple[str, str]] = set()
        self._terminal_events: dict[str, asyncio.Event] = {}
        self.tenant_limit = tenant_limit
        self.workspace_limit = workspace_limit or tenant_limit

    @staticmethod
    def request_digest(request: AIInvocationRequest) -> str:
        value = {
            "execution": request.execution_id,
            "capability_contract": request.capability_contract_version_id,
            "capability": request.capability_id,
            "prompt": request.prompt,
            "template": (request.prompt_template_ref, request.prompt_template_version_ref),
            "system": request.system_instruction_ref,
            "mode": request.response_mode.value,
            "schema": request.output_schema_ref,
            "scope": (request.tenant_id, request.workspace_id),
            "lineage": (request.correlation_id, request.causation_id),
            "authorization": {
                "actor": request.authorization.actor_id,
                "permissions": sorted(request.authorization.permissions),
                "tenant": request.authorization.tenant_id,
                "workspace": request.authorization.workspace_id,
                "policy": request.authorization.policy_id,
                "policy_version": request.authorization.policy_version_id,
            },
            "required_capabilities": sorted(request.required_capabilities),
            "quality": request.quality_tier,
            "latency": request.latency_tier,
            "token_limits": (request.max_input_tokens, request.max_output_tokens),
            "cost_limit": str(request.max_total_cost),
            "deadline": request.deadline.isoformat() if request.deadline is not None else None,
            "context": [
                (
                    item.reference,
                    item.version,
                    item.content,
                    item.relevance,
                    item.necessity_reason,
                    item.trusted,
                    item.mandatory,
                )
                for item in request.context_items
            ],
            "locale": request.locale,
            "classification": request.data_classification.value,
            "policies": (
                request.safety_policy_ref,
                request.cache_policy_ref,
                request.budget_policy_ref,
            ),
            "adapters": (sorted(request.allowed_adapters), sorted(request.blocked_adapters)),
            "residency": request.residency,
            "data_handling": sorted(request.required_data_handling),
            "security": request.minimum_security_tier,
            "fallback": (request.allow_fallback, request.max_provider_attempts),
            "repair_attempts": request.repair_attempts,
            "cache_allowed": request.cache_allowed,
            "parameters": sorted(request.deterministic_parameters),
            "workflow_ai_budget_admission": request.workflow_ai_budget_admission,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    async def accept(
        self,
        request: AIInvocationRequest,
        *,
        invocation_id: str,
        acknowledgement: ResultEnvelope,
        now: datetime,
    ) -> Acceptance:
        key = (request.tenant_id, request.workspace_id, request.idempotency_key)
        digest = self.request_digest(request)
        async with self._lock:
            replay = self.admission.get(key)
            if replay is not None:
                existing_id, existing_digest = replay
                if existing_digest != digest:
                    raise ValueError("IdempotencyKey payload conflict")
                existing = self.invocations[existing_id]
                return Acceptance(existing_id, existing.acknowledgement, True)
            self.admission[key] = (invocation_id, digest)
            self.invocations[invocation_id] = GatewayInvocation(
                request, invocation_id, acknowledgement, InvocationState.REQUESTED, now
            )
            self._terminal_events[invocation_id] = asyncio.Event()
            return Acceptance(invocation_id, acknowledgement)

    async def claim_execution(
        self, invocation_id: str, *, owner: str, now: datetime, lease: timedelta
    ) -> int | None:
        """Atomically claim an invocation, reclaiming only an expired lease."""
        async with self._lock:
            invocation = self.invocations[invocation_id]
            if invocation.terminal is not None:
                return None
            if (
                invocation.execution_owner is not None
                and invocation.execution_owner != owner
                and invocation.execution_lease_expires_at is not None
                and invocation.execution_lease_expires_at > now
            ):
                return None
            invocation.execution_owner = owner
            invocation.execution_lease_expires_at = now + lease
            invocation.claim_generation += 1
            invocation.recovery_phase = "claimed"
            return invocation.claim_generation

    async def release_execution(self, invocation_id: str, *, owner: str, generation: int) -> None:
        async with self._lock:
            invocation = self.invocations[invocation_id]
            if invocation.execution_owner == owner and invocation.claim_generation == generation:
                invocation.execution_owner = None
                invocation.execution_lease_expires_at = None

    async def renew_execution(
        self,
        invocation_id: str,
        *,
        owner: str,
        generation: int,
        now: datetime,
        lease: timedelta,
    ) -> bool:
        async with self._lock:
            invocation = self.invocations[invocation_id]
            if (
                invocation.terminal is not None
                or invocation.execution_owner != owner
                or invocation.claim_generation != generation
            ):
                return False
            invocation.execution_lease_expires_at = now + lease
            return True

    async def assert_execution_owner(
        self, invocation_id: str, *, owner: str, generation: int
    ) -> None:
        async with self._lock:
            invocation = self.invocations[invocation_id]
            if invocation.execution_owner != owner or invocation.claim_generation != generation:
                raise ExecutionOwnershipLost("AI Gateway execution ownership was lost")

    async def wait_for_terminal(
        self, invocation_id: str, *, timeout: float = 30.0
    ) -> AIInvocationResponse:
        invocation = self.invocations[invocation_id]
        if invocation.terminal is not None:
            return invocation.terminal
        event = self._terminal_events.setdefault(invocation_id, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=timeout)
        terminal = self.invocations[invocation_id].terminal
        if terminal is None:
            raise RuntimeError("execution lease ended without terminal outcome")
        return terminal

    async def reserve(
        self,
        invocation_id: str,
        tenant_id: str,
        workspace_id: str,
        amount: Decimal,
        *,
        now: datetime,
        pricing_version: str | None = None,
    ) -> GatewayReservation:
        async with self._lock:
            current = self.reservations.get(invocation_id)
            if current is not None:
                return current
            tenant_used = sum(
                reservation.amount
                for reservation in self.reservations.values()
                if reservation.state in {"pending", "committed", "usage_pending"}
                and reservation.tenant_id == tenant_id
            )
            workspace_used = sum(
                reservation.amount
                for reservation in self.reservations.values()
                if reservation.state in {"pending", "committed", "usage_pending"}
                and reservation.tenant_id == tenant_id
                and reservation.workspace_id == workspace_id
            )
            if tenant_used + amount > self.tenant_limit:
                raise ValueError("hard budget exceeded")
            if workspace_used + amount > self.workspace_limit:
                raise ValueError("workspace hard budget exceeded")
            reservation = GatewayReservation(
                invocation_id,
                tenant_id,
                workspace_id,
                amount,
                "pending",
                now + timedelta(minutes=5),
            )
            self.reservations[invocation_id] = reservation
            return reservation

    async def reconcile(
        self,
        invocation_id: str,
        actual: Decimal,
        usage: AIUsage,
        *,
        owner: str | None = None,
        generation: int | None = None,
    ) -> None:
        if owner is not None and generation is not None:
            await self.assert_execution_owner(invocation_id, owner=owner, generation=generation)
        async with self._lock:
            reservation = self.reservations[invocation_id]
            reservation.state = "committed"
            reservation.actual = actual
            self.usage[invocation_id] = usage

    async def release_expired(self, *, now: datetime) -> tuple[str, ...]:
        async with self._lock:
            released: list[str] = []
            for reservation in self.reservations.values():
                if reservation.state == "pending" and reservation.expires_at <= now:
                    reservation.state = "released"
                    released.append(reservation.invocation_id)
            return tuple(released)

    async def load(self, invocation_id: str) -> GatewayInvocation:
        """Recover an accepted invocation by its Gateway-owned identity."""
        return self.invocations[invocation_id]

    async def checkpoint(self, invocation: GatewayInvocation) -> None:
        """Durably checkpoint lifecycle/decision/terminal state in persistent stores."""
        self.invocations[invocation.invocation_id] = invocation
        if invocation.terminal is not None:
            self._terminal_events.setdefault(invocation.invocation_id, asyncio.Event()).set()

    async def checkpoint_terminal_intent(self, invocation: GatewayInvocation) -> None:
        """Persist an immutable terminal intent before authoritative terminalization."""
        if invocation.execution_owner is None or invocation.claim_generation <= 0:
            raise ExecutionOwnershipLost("terminal intent requires execution ownership")
        await self.assert_execution_owner(
            invocation.invocation_id,
            owner=invocation.execution_owner,
            generation=invocation.claim_generation,
        )
        current = self.invocations[invocation.invocation_id]
        if (
            current.terminal_intent is not None
            and current.terminal_intent != invocation.terminal_intent
        ):
            raise ExecutionOwnershipLost("immutable terminal intent already exists")
        self.invocations[invocation.invocation_id] = invocation

    async def record_usage(
        self,
        invocation_id: str,
        *,
        usage: AIUsage,
        cost: Decimal,
        event_key: str,
        kind: str,
        final: bool,
        attempt_number: int | None = None,
        owner: str | None = None,
        generation: int | None = None,
    ) -> None:
        del attempt_number
        if owner is not None and generation is not None:
            await self.assert_execution_owner(invocation_id, owner=owner, generation=generation)
        events = self._usage_events.setdefault(invocation_id, {})
        if kind == "provider_partial":
            cost = max(
                Decimal("0"), cost - sum((item[1] for item in events.values()), Decimal("0"))
            )
        events.setdefault(event_key, (usage, cost))
        cumulative_cost = sum((item[1] for item in events.values()), Decimal("0"))
        strongest = max(
            (item[0] for item in events.values()),
            key=lambda item: (not item.estimated, item.input_tokens + item.output_tokens),
        )
        reservation = self.reservations[invocation_id]
        reservation.actual = cumulative_cost
        reservation.state = "committed" if final else "usage_pending"
        self.usage[invocation_id] = strongest

    async def load_provider_effect(
        self, invocation_id: str, *, effect_key: str
    ) -> ProviderResult | None:
        return self._provider_effects.get((invocation_id, effect_key))

    async def reserve_provider_effect(
        self,
        invocation_id: str,
        *,
        effect_key: str,
        attempt_number: int,
        model_key: str,
        owner: str,
        generation: int,
    ) -> None:
        """Persist the opaque idempotency key before crossing the provider boundary."""
        del attempt_number, model_key
        await self.assert_execution_owner(invocation_id, owner=owner, generation=generation)
        self._provider_effect_reservations.add((invocation_id, effect_key))

    async def record_provider_effect(
        self,
        invocation_id: str,
        *,
        effect_key: str,
        result: ProviderResult,
        owner: str | None = None,
        generation: int | None = None,
    ) -> None:
        if owner is not None and generation is not None:
            await self.assert_execution_owner(invocation_id, owner=owner, generation=generation)
        self._provider_effects.setdefault((invocation_id, effect_key), result)

    async def cached(
        self, tenant_id: str, workspace_id: str, cache_key: str, *, now: datetime
    ) -> CachedContent | None:
        cached = self.cache.get((tenant_id, workspace_id, cache_key))
        if cached is None or cached.expires_at <= now:
            return None
        return cached

    async def cache_content(
        self, tenant_id: str, workspace_id: str, cache_key: str, value: CachedContent
    ) -> None:
        self.cache[(tenant_id, workspace_id, cache_key)] = value

    async def invalidate_cache(
        self, tenant_id: str, workspace_id: str, *, cache_key: str | None = None
    ) -> int:
        keys = [
            key
            for key in self.cache
            if key[:2] == (tenant_id, workspace_id) and (cache_key is None or key[2] == cache_key)
        ]
        for key in keys:
            del self.cache[key]
        return len(keys)

    async def release(self, invocation_id: str, *, state: str = "released") -> None:
        async with self._lock:
            reservation = self.reservations.get(invocation_id)
            if reservation is not None and reservation.state == "pending":
                reservation.state = state

    async def record_attempt(
        self,
        invocation_id: str,
        *,
        attempt_number: int,
        model_key: str,
        state: str,
        usage: AIUsage | None,
        cost: Decimal,
    ) -> None:
        self.attempts.setdefault(invocation_id, []).append(
            (attempt_number, model_key, state, usage, cost)
        )


class ReferenceAIGateway:
    """Offline, deterministic reference implementation of ES-012."""

    def __init__(
        self,
        *,
        clock: Clock,
        identifiers: IdentifierFactory,
        authorizer: ScopeAuthorizer,
        observations: ObservationRecorder,
        catalog: Sequence[ModelCatalogEntry],
        adapters: Mapping[str, ProviderAdapter],
        store: ReferenceGatewayStore | None = None,
        execution_lease: timedelta = timedelta(seconds=30),
        heartbeat_interval: float = 10.0,
        health_cooldown: timedelta = timedelta(seconds=30),
        prompt_packages: PromptPackageCatalog | None = None,
        workflow_admission_authority: WorkflowAdmissionAuthority | None = None,
    ) -> None:
        self._clock = clock
        self._identifiers = identifiers
        self._authorizer = authorizer
        self._observations = observations
        self._catalog = tuple(catalog)
        self._adapters = dict(adapters)
        self.store = store or ReferenceGatewayStore()
        self._outcomes = OutcomeFactory(clock, identifiers)
        self.lifecycle: dict[str, list[InvocationState]] = {}
        self._execution_lease = execution_lease
        self._heartbeat_interval = heartbeat_interval
        self._health_cooldown = health_cooldown
        self._health_lock = threading.Lock()
        self._cooldowns: dict[str, datetime] = {}
        self._degraded: set[str] = set()
        self._prompt_packages = prompt_packages
        self._workflow_admission_authority = workflow_admission_authority

    async def _heartbeat(
        self, invocation_id: str, owner: str, generation: int, lost: asyncio.Event
    ) -> None:
        while not lost.is_set():
            await asyncio.sleep(self._heartbeat_interval)
            try:
                renewed = await self.store.renew_execution(
                    invocation_id,
                    owner=owner,
                    generation=generation,
                    now=self._clock.now(),
                    lease=self._execution_lease,
                )
            except Exception:
                renewed = False
            if not renewed:
                lost.set()
                return

    async def _fence(
        self,
        invocation_id: str,
        owner: str,
        generation: int,
        lost: asyncio.Event | None = None,
    ) -> None:
        if lost is not None and lost.is_set():
            raise ExecutionOwnershipLost("AI Gateway execution heartbeat was lost")
        await self.store.assert_execution_owner(invocation_id, owner=owner, generation=generation)

    @property
    def observations(self) -> ObservationRecorder:
        """Expose the safe recorder for reference conformance inspection."""
        return self._observations

    async def accept(self, request: AIInvocationRequest) -> Acceptance:
        await self._preflight(request)
        invocation_id = self._identifiers.new("ai")
        acknowledgement = ResultEnvelope(
            result_id=self._identifiers.new("result"),
            result_status=ResultStatus.ACCEPTED,
            outcome_category=OutcomeCategory.ACKNOWLEDGEMENT,
            subject_reference=invocation_id,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            producer_component="AI Gateway",
            command_id=request.command_id,
            started_at=self._clock.now(),
        )
        accepted = await self.store.accept(
            request,
            invocation_id=invocation_id,
            acknowledgement=acknowledgement,
            now=self._clock.now(),
        )
        self.lifecycle.setdefault(accepted.ai_invocation_id, [InvocationState.REQUESTED])
        return accepted

    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse:
        accepted = await self.accept(request)
        try:
            invocation = await self.store.load(accepted.ai_invocation_id)
        except Exception:
            invocation = GatewayInvocation(
                request,
                accepted.ai_invocation_id,
                accepted.acknowledgement,
                InvocationState.REQUESTED,
                accepted.acknowledgement.started_at or self._clock.now(),
            )
        if invocation.terminal is not None:
            return invocation.terminal
        try:
            return await self.execute(accepted.ai_invocation_id)
        except Exception:
            response = self._failure(
                invocation, ProviderFailure("AI_GATEWAY_PERSISTENCE_FAILURE", retryable=False)
            )
            invocation.terminal_intent = response
            invocation.recovery_phase = "terminalization_pending"
            with suppress(Exception):
                await self.store.checkpoint_terminal_intent(invocation)
                await self.store.checkpoint(invocation)
            return response

    async def execute(self, invocation_id: str) -> AIInvocationResponse:
        invocation = await self.store.load(invocation_id)
        request = invocation.request
        if invocation.terminal is not None:
            return invocation.terminal
        owner = f"gateway-worker:{uuid4().hex}"
        generation = await self.store.claim_execution(
            invocation_id, owner=owner, now=self._clock.now(), lease=self._execution_lease
        )
        if generation is None:
            return await self.store.wait_for_terminal(invocation_id)
        invocation.execution_owner = owner
        invocation.execution_lease_expires_at = self._clock.now() + self._execution_lease
        invocation.claim_generation = generation
        invocation.recovery_phase = "claimed"
        if invocation.terminal_intent is not None:
            invocation.terminal = invocation.terminal_intent
            invocation.recovery_phase = "terminalization_pending"
            try:
                await self.store.checkpoint(invocation)
            finally:
                await self.store.release_execution(
                    invocation_id, owner=owner, generation=generation
                )
            return invocation.terminal
        reservation: GatewayReservation | None = None
        ownership_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(invocation_id, owner, generation, ownership_lost)
        )
        try:
            self._transition(invocation, InvocationState.POLICY_VALIDATED)
            prompt, tokens, context_digest = self._assemble(request)
            self._observe(
                invocation,
                "ai.context.prepared",
                {"input_tokens": tokens, "context_digest": context_digest, "stage": 0},
            )
            route = (
                invocation.provider_sequence[
                    min(invocation.next_provider_attempt - 1, len(invocation.provider_sequence) - 1)
                ]
                if invocation.provider_sequence
                else self._route(request, tokens)
            )
            cache_key = self._cache_key(request, context_digest, route)
            invocation.route = route
            self._observe(
                invocation,
                "ai.route.decided",
                {
                    "model_key": route.model_key,
                    "adapter_key": route.adapter_key,
                    "decision_reference": route.decision_reference,
                    "excluded": dict(route.excluded),
                },
            )
            self._transition(invocation, InvocationState.PROVIDER_SELECTED)
            await self.store.checkpoint(invocation)
            reservation = await self.store.reserve(
                invocation_id,
                request.tenant_id,
                request.workspace_id,
                request.max_total_cost,
                now=self._clock.now(),
                pricing_version=self._model(route.model_key).pricing_version,
            )
            self._transition(invocation, InvocationState.PREPARED)
            self._observe(
                invocation,
                "ai.budget.reserved",
                {
                    "amount": str(reservation.amount),
                    "expires_at": reservation.expires_at.isoformat(),
                },
            )
            await self.store.checkpoint(invocation)
            if invocation.provider_sequence:
                candidates = list(invocation.provider_sequence)
            else:
                candidates = [route]
                if request.allow_fallback:
                    candidates.extend(
                        candidate
                        for candidate in self._eligible_routes(request, tokens)
                        if candidate.model_key != route.model_key
                    )
                invocation.provider_sequence = tuple(candidates[: request.max_provider_attempts])
                await self.store.checkpoint(invocation)
            last_failure = (
                ProviderFailure(
                    invocation.last_provider_failure_code,
                    retryable=invocation.last_provider_failure_retryable,
                )
                if invocation.last_provider_failure_code is not None
                else None
            )
            spent = invocation.cumulative_cost
            total_input = invocation.cumulative_usage.input_tokens
            total_output = invocation.cumulative_usage.output_tokens
            total_cached = invocation.cumulative_usage.cached_tokens
            total_reasoning = invocation.cumulative_usage.reasoning_tokens
            for attempts, candidate in enumerate(
                candidates[invocation.next_provider_attempt - 1 : request.max_provider_attempts],
                start=invocation.next_provider_attempt,
            ):
                if candidate.estimated_cost > request.max_total_cost - spent:
                    last_failure = ProviderFailure("AI_FALLBACK_BUDGET_EXHAUSTED", retryable=False)
                    break
                if request.deadline is not None and self._clock.now() >= request.deadline:
                    last_failure = ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=False)
                    break
                if attempts > 1:
                    self._transition(invocation, InvocationState.RETRYING)
                invocation.route = candidate
                await self.store.checkpoint(invocation)
                cache_key = self._cache_key(request, context_digest, candidate)
                cached = None
                if self._cache_eligible(request):
                    cached = await self.store.cached(
                        request.tenant_id,
                        request.workspace_id,
                        cache_key,
                        now=self._clock.now(),
                    )
                if cached is not None and cached.expires_at > self._clock.now():
                    usage = AIUsage(
                        total_input + cached.usage.input_tokens,
                        total_output + cached.usage.output_tokens,
                        cached_tokens=cached.usage.input_tokens,
                        reasoning_tokens=cached.usage.reasoning_tokens,
                        estimated=cached.usage.estimated,
                    )
                    await self.store.reconcile(
                        invocation_id,
                        spent,
                        usage,
                        owner=owner,
                        generation=generation,
                    )
                    response = self._success(
                        invocation, cached.content, usage, cached.route or candidate, cache_hit=True
                    )
                    self._observe(
                        invocation,
                        "ai.cache.hit",
                        {
                            "cache_key": cache_key,
                            "estimated_savings": str(candidate.estimated_cost),
                            "cached_tokens": usage.cached_tokens,
                        },
                    )
                    await self.store.checkpoint(invocation)
                    return response
                self._transition(invocation, InvocationState.INVOKED)
                self._observe(
                    invocation,
                    "ai.provider.attempt",
                    {
                        "attempt": attempts,
                        "model_key": candidate.model_key,
                        "adapter_key": candidate.adapter_key,
                        "provider_health": self._health_state(candidate.model_key),
                        "prior_spend": str(spent),
                        "remaining_budget": str(request.max_total_cost - spent),
                        "failover_reason": (
                            last_failure.code if attempts > 1 and last_failure is not None else ""
                        ),
                    },
                )
                adapter = self._adapters[candidate.adapter_key]
                try:
                    effect_key = f"{invocation_id}:provider:{attempts}"
                    provider_result = await self.store.load_provider_effect(
                        invocation_id, effect_key=effect_key
                    )
                    if provider_result is None:
                        # Make the provider crossing recoverable before it starts.
                        # A restarted worker can now distinguish a never-started
                        # attempt from one whose provider-side completion must be
                        # recovered through the adapter's durable effect boundary.
                        await self.store.reserve_provider_effect(
                            invocation_id,
                            effect_key=effect_key,
                            attempt_number=attempts,
                            model_key=candidate.model_key,
                            owner=owner,
                            generation=generation,
                        )
                        invocation_call = adapter.invoke(
                            model_key=candidate.model_key,
                            prompt=prompt,
                            request=request,
                            effect_key=effect_key,
                        )
                        if request.deadline is None:
                            provider_result = await invocation_call
                        else:
                            remaining = (request.deadline - self._clock.now()).total_seconds()
                            try:
                                provider_result = await asyncio.wait_for(
                                    invocation_call, timeout=max(0, remaining)
                                )
                            except TimeoutError:
                                raise ProviderFailure(
                                    "AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False
                                ) from None
                        await self._fence(invocation_id, owner, generation, ownership_lost)
                        await self.store.record_provider_effect(
                            invocation_id,
                            effect_key=effect_key,
                            result=provider_result,
                            owner=owner,
                            generation=generation,
                        )
                    await self._fence(invocation_id, owner, generation, ownership_lost)
                    usage = provider_result.usage or AIUsage(
                        tokens, request.max_output_tokens, estimated=True
                    )
                    attempt_cost = self._usage_cost(candidate.model_key, usage)
                    # Admission is based on the governed envelope, but the
                    # provider's metered result remains authoritative for the
                    # actual execution.  Never turn a post-admission output
                    # or cost overrun into a successful terminal response.
                    if usage.output_tokens > request.max_output_tokens:
                        raise ProviderFailure(
                            "AI_OUTPUT_LIMIT_EXCEEDED", retryable=False, usage=usage
                        )
                    if attempt_cost > request.max_total_cost - spent:
                        raise ProviderFailure("AI_BUDGET_OVERRUN", retryable=False, usage=usage)
                    spent += attempt_cost
                    total_input += usage.input_tokens
                    total_output += usage.output_tokens
                    total_cached += usage.cached_tokens
                    total_reasoning += usage.reasoning_tokens
                    await self.store.record_attempt(
                        invocation_id,
                        attempt_number=attempts,
                        model_key=candidate.model_key,
                        state=(
                            "low_confidence"
                            if provider_result.confidence < Decimal("0.5")
                            else "completed"
                        ),
                        usage=usage,
                        cost=attempt_cost,
                    )
                    self._observe(
                        invocation,
                        "ai.provider.usage",
                        {
                            "attempt": attempts,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "cost": str(attempt_cost),
                            "cumulative_cost": str(spent),
                            "adapter_key": candidate.adapter_key,
                        },
                    )
                    content, repair_usage, repair_cost = await self._validate_and_repair(
                        invocation,
                        request=request,
                        content=provider_result.content,
                        adapter=adapter,
                        candidate=candidate,
                        provider_attempt=attempts,
                        remaining_cost=request.max_total_cost - spent,
                        owner=owner,
                        generation=generation,
                        ownership_lost=ownership_lost,
                    )
                    spent += repair_cost
                    total_input += repair_usage.input_tokens
                    total_output += repair_usage.output_tokens
                    total_cached += repair_usage.cached_tokens
                    total_reasoning += repair_usage.reasoning_tokens
                    self._record_health_success(candidate.model_key)
                    if provider_result.confidence < Decimal("0.5"):
                        last_failure = ProviderFailure("AI_LOW_CONFIDENCE", retryable=True)
                        invocation.cumulative_cost = spent
                        invocation.cumulative_usage = AIUsage(
                            total_input,
                            total_output,
                            cached_tokens=total_cached,
                            reasoning_tokens=total_reasoning,
                        )
                        invocation.next_provider_attempt = attempts + 1
                        invocation.last_provider_failure_code = last_failure.code
                        invocation.last_provider_failure_retryable = True
                        prompt, tokens, context_digest = self._assemble(request, stage=1)
                        self._observe(
                            invocation,
                            "ai.context.escalated",
                            {"stage": 1, "input_tokens": tokens, "signal": "low_confidence"},
                        )
                        await self.store.checkpoint(invocation)
                        continue
                    cumulative_usage = AIUsage(
                        total_input,
                        total_output,
                        cached_tokens=total_cached,
                        reasoning_tokens=total_reasoning,
                    )
                    invocation.cumulative_cost = spent
                    invocation.cumulative_usage = cumulative_usage
                    await self._fence(invocation_id, owner, generation, ownership_lost)
                    await self.store.reconcile(
                        invocation_id,
                        spent,
                        cumulative_usage,
                        owner=owner,
                        generation=generation,
                    )
                    if self._cache_eligible(request):
                        cache_key = self._cache_key(request, context_digest, candidate)
                        await self.store.cache_content(
                            request.tenant_id,
                            request.workspace_id,
                            cache_key,
                            CachedContent(
                                content,
                                usage,
                                self._clock.now() + timedelta(minutes=10),
                                invocation_id,
                                candidate,
                            ),
                        )
                    response = self._success(invocation, content, cumulative_usage, candidate)
                    await self.store.checkpoint(invocation)
                    return response
                except ProviderFailure as failure:
                    failure_cost = Decimal("0")
                    if failure.usage is not None:
                        failure_cost = self._usage_cost(candidate.model_key, failure.usage)
                        spent += failure_cost
                        total_input += failure.usage.input_tokens
                        total_output += failure.usage.output_tokens
                        total_cached += failure.usage.cached_tokens
                        total_reasoning += failure.usage.reasoning_tokens
                    await self.store.record_attempt(
                        invocation_id,
                        attempt_number=attempts,
                        model_key=candidate.model_key,
                        state="failed",
                        usage=failure.usage,
                        cost=failure_cost,
                    )
                    self._observe(
                        invocation,
                        "ai.provider.failure",
                        {
                            "attempt": attempts,
                            "code": failure.code,
                            "retryable": failure.retryable,
                            "cost": str(failure_cost),
                            "cumulative_cost": str(spent),
                            "adapter_key": candidate.adapter_key,
                        },
                    )
                    last_failure = failure
                    invocation.cumulative_cost = spent
                    invocation.cumulative_usage = AIUsage(
                        total_input,
                        total_output,
                        cached_tokens=total_cached,
                        reasoning_tokens=total_reasoning,
                    )
                    invocation.next_provider_attempt = attempts + 1
                    invocation.last_provider_failure_code = failure.code
                    invocation.last_provider_failure_retryable = failure.retryable
                    self._record_health_failure(candidate.model_key, failure)
                    await self.store.checkpoint(invocation)
                    if not failure.retryable:
                        break
            if spent > 0:
                await self.store.reconcile(
                    invocation_id,
                    spent,
                    invocation.cumulative_usage,
                    owner=owner,
                    generation=generation,
                )
            else:
                await self.store.release(invocation_id)
            response = self._failure(
                invocation, last_failure or ProviderFailure("AI_GATEWAY_FAILURE", retryable=False)
            )
            if total_input > 0 or total_output > 0:
                response = replace(
                    response,
                    usage=invocation.cumulative_usage,
                )
                invocation.terminal = response
            await self.store.checkpoint(invocation)
            return response
        except asyncio.CancelledError:
            if reservation is not None:
                await self.store.release(invocation_id)
            response = self._failure(
                invocation, ProviderFailure("AI_PROVIDER_CANCELLED", retryable=False)
            )
            invocation.terminal_intent = response
            invocation.recovery_phase = "terminalization_pending"
            with suppress(Exception):
                await self.store.checkpoint_terminal_intent(invocation)
                await self.store.checkpoint(invocation)
            return response
        except Exception as error:
            if isinstance(error, ExecutionOwnershipLost):
                return await self.store.wait_for_terminal(invocation_id)
            if reservation is not None:
                with suppress(Exception):
                    await self.store.release(invocation_id)
            error_text = str(error).lower()
            code = invocation.last_provider_failure_code or (
                "AI_INPUT_LIMIT_EXCEEDED"
                if "input ceiling" in error_text
                else "AI_GATEWAY_PERSISTENCE_FAILURE"
                if "persist" in error_text
                else "AI_GATEWAY_FAILURE"
            )
            response = self._failure(invocation, ProviderFailure(code, retryable=False))
            invocation.terminal_intent = response
            invocation.recovery_phase = "terminalization_pending"
            with suppress(Exception):
                await self.store.checkpoint_terminal_intent(invocation)
                await self.store.checkpoint(invocation)
            return response
        finally:
            ownership_lost.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            with suppress(Exception):
                await self.store.release_execution(
                    invocation_id, owner=owner, generation=generation
                )

    async def stream(self, request: AIInvocationRequest) -> AsyncIterator[StreamChunk]:
        accepted = await self.accept(replace(request, response_mode=ResponseMode.STREAM))
        invocation = await self.store.load(accepted.ai_invocation_id)
        yield StreamChunk(accepted.ai_invocation_id, 0, "acknowledgement")
        sequence = 1
        if invocation.terminal is not None:
            yield StreamChunk(
                invocation.invocation_id,
                sequence,
                "terminal",
                usage=invocation.terminal.usage,
                terminal=invocation.terminal,
            )
            return
        owner = f"gateway-stream-worker:{uuid4().hex}"
        generation = await self.store.claim_execution(
            invocation.invocation_id,
            owner=owner,
            now=self._clock.now(),
            lease=self._execution_lease,
        )
        if generation is None:
            terminal = await self.store.wait_for_terminal(invocation.invocation_id)
            yield StreamChunk(
                invocation.invocation_id,
                sequence,
                "terminal",
                usage=terminal.usage,
                terminal=terminal,
            )
            return
        invocation.execution_owner = owner
        invocation.execution_lease_expires_at = self._clock.now() + self._execution_lease
        invocation.claim_generation = generation
        invocation.recovery_phase = "claimed"
        reservation: GatewayReservation | None = None
        parts: list[str] = []
        input_tokens = 0
        partial_usage: AIUsage | None = None
        ownership_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(invocation.invocation_id, owner, generation, ownership_lost)
        )
        if invocation.terminal_intent is not None:
            invocation.terminal = invocation.terminal_intent
            with suppress(Exception):
                await self.store.checkpoint(invocation)
            ownership_lost.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            with suppress(Exception):
                await self.store.release_execution(
                    invocation.invocation_id, owner=owner, generation=generation
                )
            yield StreamChunk(
                invocation.invocation_id,
                sequence,
                "terminal",
                usage=invocation.terminal.usage,
                terminal=invocation.terminal,
            )
            return
        try:
            if invocation.stream_started:
                partial_usage = invocation.stream_usage
                parts = list(invocation.stream_content)
                raise ProviderFailure("AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False)
            self._transition(invocation, InvocationState.POLICY_VALIDATED)
            prompt, tokens, _ = self._assemble(invocation.request)
            input_tokens = tokens
            route = self._route(invocation.request, tokens)
            invocation.route = route
            self._transition(invocation, InvocationState.PROVIDER_SELECTED)
            await self.store.checkpoint(invocation)
            reservation = await self.store.reserve(
                invocation.invocation_id,
                invocation.request.tenant_id,
                invocation.request.workspace_id,
                invocation.request.max_total_cost,
                now=self._clock.now(),
                pricing_version=self._model(route.model_key).pricing_version,
            )
            self._transition(invocation, InvocationState.PREPARED)
            await self.store.checkpoint(invocation)
            self._transition(invocation, InvocationState.INVOKED)
            self._transition(invocation, InvocationState.STREAMING)
            invocation.stream_started = True
            invocation.stream_sequence = sequence
            await self.store.checkpoint(invocation)
            self._observe(
                invocation,
                "ai.stream.started",
                {"model_key": route.model_key},
            )
            yield StreamChunk(invocation.invocation_id, sequence, "stream_start")
            sequence += 1
            usage: AIUsage | None = None
            adapter = self._adapters[route.adapter_key]
            timeout_seconds = (
                None
                if invocation.request.deadline is None
                else max(0, (invocation.request.deadline - self._clock.now()).total_seconds())
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    async for event in adapter.stream(
                        model_key=route.model_key, prompt=prompt, request=invocation.request
                    ):
                        if event.kind == "content_delta" and event.content:
                            candidate_content = "".join((*parts, event.content))
                            if (
                                self._estimate(candidate_content)
                                > invocation.request.max_output_tokens
                            ):
                                raise ProviderFailure("AI_OUTPUT_LIMIT_EXCEEDED", retryable=False)
                            parts.append(event.content)
                            invocation.stream_content = tuple(parts)
                            invocation.stream_sequence = sequence
                            await self._fence(
                                invocation.invocation_id,
                                owner,
                                generation,
                                ownership_lost,
                            )
                            await self.store.checkpoint(invocation)
                            yield StreamChunk(
                                invocation.invocation_id,
                                sequence,
                                "content_delta",
                                event.content,
                            )
                            sequence += 1
                        elif event.kind == "usage":
                            reported = event.usage
                            if reported is None:
                                continue
                            if usage is not None and (
                                reported.input_tokens < usage.input_tokens
                                or reported.output_tokens < usage.output_tokens
                            ):
                                raise ProviderFailure(
                                    "AI_NON_MONOTONIC_USAGE", retryable=False, usage=usage
                                )
                            usage = reported
                            partial_usage = usage
                            invocation.stream_usage = usage
                            partial_cost = self._usage_cost(route.model_key, usage)
                            await self.store.record_usage(
                                invocation.invocation_id,
                                usage=usage,
                                cost=partial_cost,
                                event_key=f"stream:1:{usage.input_tokens}:{usage.output_tokens}",
                                kind="provider_partial",
                                final=False,
                                attempt_number=1,
                                owner=owner,
                                generation=generation,
                            )
                            invocation.stream_sequence = sequence
                            await self._fence(
                                invocation.invocation_id,
                                owner,
                                generation,
                                ownership_lost,
                            )
                            await self.store.checkpoint(invocation)
                            yield StreamChunk(
                                invocation.invocation_id, sequence, "usage", usage=usage
                            )
                            sequence += 1
                            self._observe(
                                invocation,
                                "ai.stream.usage",
                                {
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                },
                            )
            except TimeoutError:
                # The stream is durably marked started before entering the deadline.
                # Its provider effect may therefore have occurred.
                raise ProviderFailure("AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False) from None
            content = "".join(parts)
            if self._estimate(content) > invocation.request.max_output_tokens:
                raise ProviderFailure("AI_OUTPUT_LIMIT_EXCEEDED", retryable=False)
            usage = usage or AIUsage(tokens, self._estimate(content), estimated=True)
            actual = self._usage_cost(route.model_key, usage)
            if actual > invocation.request.max_total_cost:
                raise ProviderFailure("AI_BUDGET_OVERRUN", retryable=False)
            await self.store.record_attempt(
                invocation.invocation_id,
                attempt_number=1,
                model_key=route.model_key,
                state="completed",
                usage=usage,
                cost=actual,
            )
            await self.store.reconcile(
                invocation.invocation_id,
                actual,
                usage,
                owner=owner,
                generation=generation,
            )
            response = self._success(invocation, content, usage, route)
        except asyncio.CancelledError:
            if reservation is not None:
                if parts and invocation.route is not None:
                    partial = partial_usage or AIUsage(
                        input_tokens, self._estimate("".join(parts)), estimated=True
                    )
                    partial_usage = partial
                    cost = self._usage_cost(invocation.route.model_key, partial)
                    await self.store.record_attempt(
                        invocation.invocation_id,
                        attempt_number=1,
                        model_key=invocation.route.model_key,
                        state="cancelled_partial",
                        usage=partial,
                        cost=cost,
                    )
                    await self.store.reconcile(
                        invocation.invocation_id,
                        cost,
                        partial,
                        owner=owner,
                        generation=generation,
                    )
                else:
                    await self.store.release(invocation.invocation_id)
            response = self._failure(
                invocation, ProviderFailure("AI_PROVIDER_CANCELLED", retryable=False)
            )
            if partial_usage is not None:
                response = replace(response, usage=partial_usage)
                invocation.terminal = response
        except Exception as error:
            if isinstance(error, ExecutionOwnershipLost):
                ownership_lost.set()
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
                terminal = await self.store.wait_for_terminal(invocation.invocation_id)
                yield StreamChunk(
                    invocation.invocation_id,
                    sequence,
                    "terminal",
                    usage=terminal.usage,
                    terminal=terminal,
                )
                return
            failure = (
                error
                if isinstance(error, ProviderFailure)
                else ProviderFailure("AI_STREAM_FAILURE", retryable=False)
            )
            if failure.usage is not None:
                partial_usage = failure.usage
            if reservation is not None:
                if (parts or partial_usage is not None) and invocation.route is not None:
                    partial = partial_usage or AIUsage(
                        input_tokens, self._estimate("".join(parts)), estimated=True
                    )
                    partial_usage = partial
                    cost = self._usage_cost(invocation.route.model_key, partial)
                    await self.store.record_attempt(
                        invocation.invocation_id,
                        attempt_number=1,
                        model_key=invocation.route.model_key,
                        state="failed_partial",
                        usage=partial,
                        cost=cost,
                    )
                    await self.store.reconcile(
                        invocation.invocation_id,
                        cost,
                        partial,
                        owner=owner,
                        generation=generation,
                    )
                else:
                    await self.store.release(invocation.invocation_id)
            response = self._failure(invocation, failure)
            if partial_usage is not None:
                response = replace(response, usage=partial_usage)
                invocation.terminal = response
        except BaseException:
            ownership_lost.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            raise
        invocation.terminal_intent = response
        invocation.recovery_phase = "terminalization_pending"
        with suppress(Exception):
            await self.store.checkpoint_terminal_intent(invocation)
            await self._fence(invocation.invocation_id, owner, generation, ownership_lost)
            await self.store.checkpoint(invocation)
        ownership_lost.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        with suppress(Exception):
            await self.store.release_execution(
                invocation.invocation_id, owner=owner, generation=generation
            )
        yield StreamChunk(
            invocation.invocation_id,
            sequence,
            "terminal",
            usage=response.usage,
            terminal=response,
        )

    async def _preflight(self, request: AIInvocationRequest) -> None:
        self._authorizer.require(
            request.authorization,
            permission="ai.invoke",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
        )
        if not request.command_id or not request.idempotency_key or not request.prompt.strip():
            raise ValueError("invalid admission envelope")
        if request.max_input_tokens <= 0 or request.max_output_tokens <= 0:
            raise ValueError("token budgets must be positive")
        if request.max_total_cost <= 0:
            raise ValueError("coarse budget feasibility failed")
        if (request.output_schema is None) != (request.output_schema_identity is None):
            raise ValueError("governed schema material and identity must be bound together")
        if request.workflow_ai_budget_admission is not None:
            await self._validate_workflow_admission(request)

    async def _validate_workflow_admission(self, request: AIInvocationRequest) -> None:
        binding = request.workflow_ai_budget_admission
        required = {
            "BindingContractVersion",
            "TenantId",
            "WorkspaceId",
            "WorkflowId",
            "WorkflowStepId",
            "CommandId",
            "ExecutionId",
            "WorkflowDefinitionVersionId",
            "PolicyId",
            "PolicyVersionId",
            "WorkflowAdmissionStateVersion",
            "GatewayIdempotencyKey",
            "CommittedExposure",
            "CapabilityBinding",
        }
        if not isinstance(binding, Mapping) or set(binding) != required:
            raise ValueError("committed Workflow AI admission binding is required")
        raw_capability = binding.get("CapabilityBinding")
        raw_exposure = binding.get("CommittedExposure")
        state_version = binding.get("WorkflowAdmissionStateVersion")
        if not isinstance(raw_capability, Mapping) or not isinstance(raw_exposure, Mapping):
            raise ValueError("Workflow AI admission binding does not match Gateway request")
        capability = cast(Mapping[str, object], raw_capability)
        exposure = cast(Mapping[str, object], raw_exposure)
        if (
            binding.get("BindingContractVersion") != 1
            or binding.get("TenantId") != request.tenant_id
            or binding.get("WorkspaceId") != request.workspace_id
            or binding.get("WorkflowId") != request.workflow_id
            or binding.get("WorkflowStepId") != request.workflow_step_id
            or binding.get("WorkflowDefinitionVersionId") != request.workflow_definition_version_id
            or binding.get("CommandId") != request.command_id
            or binding.get("ExecutionId") != request.execution_id
            or binding.get("PolicyId") != request.authorization.policy_id
            or binding.get("PolicyVersionId") != request.authorization.policy_version_id
            or binding.get("GatewayIdempotencyKey") != request.idempotency_key
            or not isinstance(state_version, int)
            or state_version < 1
            or capability
            != {
                "SkillVersionId": request.skill_version_id,
                "CapabilityId": request.capability_id,
                "CapabilityContractVersionId": request.capability_contract_version_id,
            }
            or set(exposure) != {"Amount", "CurrencyOrReferenceUnit"}
            or exposure.get("CurrencyOrReferenceUnit") != "USD"
            or not isinstance(exposure.get("Amount"), str)
        ):
            raise ValueError("Workflow AI admission binding does not match Gateway request")
        scale6_amount = cast(str, exposure["Amount"])
        if (
            re.fullmatch(
                r"^(?:0\.[0-9]{0,5}[1-9]|[1-9][0-9]*(?:\.[0-9]{0,5}[1-9])?)$",
                scale6_amount,
            )
            is None
        ):
            raise ValueError("Workflow AI admission binding does not match Gateway request")
        authority = self._workflow_admission_authority
        if authority is None:
            raise ValueError("authoritative Workflow AI admission is unavailable")
        assert request.workflow_id is not None
        authoritative = await authority.authoritative_ai_admission(
            workflow_id=request.workflow_id,
            command_id=request.command_id,
            execution_id=request.execution_id,
        )
        if authoritative != binding:
            raise ValueError("authoritative Workflow AI admission does not match Gateway request")

    def _assemble(self, request: AIInvocationRequest, *, stage: int = 0) -> tuple[str, int, str]:
        if (
            self._prompt_packages is not None
            and request.prompt_template_ref == "structured-task-kind"
        ):
            if stage != 0 or request.context_items:
                raise ValueError("governed Stage 1 package forbids history or context")
            assembled = self._prompt_packages.assemble(
                request.prompt_template_ref,
                request.prompt_template_version_ref,
                {"statement": request.prompt},
            )
            if assembled.output_schema_reference != request.output_schema_ref:
                raise ValueError("governed schema reference does not match the prompt package")
            if (
                request.output_schema != assembled.output_schema
                or request.output_schema_identity != assembled.package_identity
            ):
                raise ValueError(
                    "governed schema material does not match immutable package binding"
                )
            content = assembled.content
            tokens = self._estimate(content)
            if tokens > request.max_input_tokens:
                raise ValueError("assembled governed prompt exceeds its input ceiling")
            return content, tokens, assembled.package_identity
        unique: dict[tuple[str, str], ContextItem] = {}
        for item in sorted(
            request.context_items,
            key=lambda value: (-value.mandatory, -value.relevance, value.reference),
        ):
            unique.setdefault((item.reference, item.version), item)
        selected: list[ContextItem] = []
        base_sections = [
            f"<system ref='{request.system_instruction_ref}'>approved instructions</system>",
            f"<task>{request.prompt.strip()}</task>",
        ]

        def render(items: Sequence[ContextItem]) -> str:
            evidence = [
                "<evidence "
                f"ref='{item.reference}' trusted='{str(item.trusted).lower()}' "
                f"reason='{item.necessity_reason}'>{item.content}</evidence>"
                for item in items
            ]
            return "\n".join((*base_sections, *evidence))

        optional_relevance = max(
            (item.relevance for item in unique.values() if not item.mandatory), default=None
        )
        for item in unique.values():
            if (
                not item.mandatory
                and stage == 0
                and optional_relevance is not None
                and item.relevance < optional_relevance
            ):
                continue
            if item.mandatory:
                selected.append(item)
                continue
            if self._estimate(render((*selected, item))) <= request.max_input_tokens:
                selected.append(item)
                continue
            low, high = 0, len(item.content)
            while low < high:
                middle = (low + high + 1) // 2
                truncated = replace(item, content=item.content[:middle])
                if self._estimate(render((*selected, truncated))) <= request.max_input_tokens:
                    low = middle
                else:
                    high = middle - 1
            if low:
                selected.append(replace(item, content=item.content[:low]))
        prompt = render(selected)
        if self._estimate(prompt) > request.max_input_tokens:
            raise ValueError("mandatory context exceeds input token limit")
        # Cache identity is content-addressed from the exact canonical request sent to
        # the adapter, not merely from mutable references to its source material.
        canonical_context = {
            "prompt": prompt,
            "stage": stage,
            "max_input_tokens": request.max_input_tokens,
            "selected": [
                {
                    "reference": item.reference,
                    "version": item.version,
                    "content": item.content,
                    "trusted": item.trusted,
                    "mandatory": item.mandatory,
                    "necessity_reason": item.necessity_reason,
                }
                for item in selected
            ],
        }
        digest = hashlib.sha256(
            json.dumps(canonical_context, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return prompt, self._estimate(prompt), digest

    @staticmethod
    def _estimate(text: str) -> int:
        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    def _eligible_routes(
        self, request: AIInvocationRequest, input_tokens: int
    ) -> list[RouteDecision]:
        excluded: dict[str, str] = {}
        eligible_entries: list[tuple[ModelCatalogEntry, Decimal]] = []
        for entry in self._catalog:
            reason = None
            health_state = self._health_state(entry.model_key)
            if entry.deprecated or not entry.healthy:
                reason = "unavailable"
            elif not entry.available:
                reason = "availability"
            elif health_state in {"rate_limited", "cooldown"}:
                reason = health_state
            elif not request.required_capabilities <= entry.capabilities:
                reason = "capability"
            elif request.quality_tier > entry.quality_tier:
                reason = "quality"
            elif input_tokens + request.max_output_tokens > entry.context_limit:
                reason = "context"
            elif request.max_output_tokens > entry.max_output:
                reason = "output"
            elif request.allowed_adapters and entry.adapter_key not in request.allowed_adapters:
                reason = "adapter_policy"
            elif entry.adapter_key in request.blocked_adapters:
                reason = "security_policy"
            elif request.residency != "any" and request.residency not in entry.residencies:
                reason = "residency"
            elif not request.required_data_handling <= entry.data_handling:
                reason = "data_handling"
            elif request.minimum_security_tier > entry.security_tier:
                reason = "security"
            elif entry.latency_tier > request.latency_tier:
                reason = "latency"
            cost = entry.estimate_cost(input_tokens, request.max_output_tokens)
            if reason is None and cost > request.max_total_cost:
                reason = "budget"
            if reason is not None:
                excluded[entry.model_key] = reason
                continue
            eligible_entries.append((entry, cost))
        decisions: list[RouteDecision] = []
        considered = tuple(sorted(model.model_key for model in self._catalog))
        for entry, cost in eligible_entries:
            decision_hash = hashlib.sha256(
                (
                    entry.model_key
                    + entry.pricing_version
                    + str(input_tokens)
                    + json.dumps(excluded, sort_keys=True)
                ).encode()
            ).hexdigest()[:12]
            decisions.append(
                RouteDecision(
                    decision_reference=f"route:{decision_hash}",
                    model_key=entry.model_key,
                    adapter_key=entry.adapter_key,
                    considered=considered,
                    excluded=dict(excluded),
                    estimated_cost=cost,
                    reason="cheapest capable model satisfying all hard constraints",
                )
            )
        return sorted(
            decisions,
            key=lambda value: (
                value.estimated_cost,
                self._model(value.model_key).latency_tier,
                value.model_key,
            ),
        )

    def _route(self, request: AIInvocationRequest, input_tokens: int) -> RouteDecision:
        eligible = self._eligible_routes(request, input_tokens)
        if not eligible:
            raise ValueError("no eligible model")
        return eligible[0]

    def _model(self, key: str) -> ModelCatalogEntry:
        return next(entry for entry in self._catalog if entry.model_key == key)

    def _usage_cost(self, model_key: str, usage: AIUsage) -> Decimal:
        return self._model(model_key).estimate_cost(
            usage.input_tokens,
            usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )

    def _health_state(self, model_key: str) -> str:
        now = self._clock.now()
        with self._health_lock:
            cooldown = self._cooldowns.get(model_key)
            if cooldown is not None:
                if cooldown > now:
                    return "cooldown"
                self._cooldowns.pop(model_key, None)
            return "degraded" if model_key in self._degraded else "healthy"

    def _record_health_failure(self, model_key: str, failure: ProviderFailure) -> None:
        if failure.code not in {
            "AI_PROVIDER_RATE_LIMITED",
            "AI_PROVIDER_QUOTA_EXHAUSTED",
            "AI_PROVIDER_OVERLOADED",
            "AI_PROVIDER_TRANSIENT_FAILURE",
        }:
            return
        with self._health_lock:
            self._degraded.add(model_key)
            self._cooldowns[model_key] = self._clock.now() + self._health_cooldown

    def _record_health_success(self, model_key: str) -> None:
        with self._health_lock:
            self._degraded.discard(model_key)
            self._cooldowns.pop(model_key, None)

    @staticmethod
    def _cache_key(request: AIInvocationRequest, context_digest: str, route: RouteDecision) -> str:
        model = route.model_key
        values = (
            request.prompt_template_ref,
            request.prompt_template_version_ref,
            request.prompt.strip(),
            ",".join(sorted(request.required_capabilities)),
            context_digest,
            request.output_schema_ref or "none",
            request.system_instruction_ref,
            request.safety_policy_ref,
            request.cache_policy_ref,
            request.budget_policy_ref,
            request.data_classification.value,
            request.residency,
            ",".join(sorted(request.required_data_handling)),
            ",".join(sorted(request.allowed_adapters)),
            ",".join(sorted(request.blocked_adapters)),
            str(request.minimum_security_tier),
            request.locale,
            str(request.max_input_tokens),
            str(request.max_output_tokens),
            str(request.quality_tier),
            str(request.latency_tier),
            repr(sorted(request.deterministic_parameters)),
            model,
            route.adapter_key,
        )
        return hashlib.sha256("|".join(values).encode()).hexdigest()

    @staticmethod
    def _cache_eligible(request: AIInvocationRequest) -> bool:
        return (
            request.cache_allowed
            and request.cache_policy_ref not in {"no-store", "cache-disabled"}
            and request.data_classification
            in {
                DataClassification.NON_SENSITIVE,
                DataClassification.INTERNAL,
            }
        )

    async def _validate_and_repair(
        self,
        invocation: GatewayInvocation,
        *,
        request: AIInvocationRequest,
        content: str,
        adapter: ProviderAdapter,
        candidate: RouteDecision,
        provider_attempt: int,
        remaining_cost: Decimal,
        owner: str,
        generation: int,
        ownership_lost: asyncio.Event,
    ) -> tuple[str, AIUsage, Decimal]:
        """Validate, then perform bounded provider-neutral and metered repair attempts."""
        if request.response_mode is not ResponseMode.STRUCTURED:
            if ReferenceAIGateway._estimate(content) > request.max_output_tokens:
                raise ProviderFailure("AI_OUTPUT_LIMIT_EXCEEDED", retryable=False)
            return content, AIUsage(0, 0), Decimal("0")
        repair_input = repair_output = 0
        repair_cost = Decimal("0")
        for repair_attempt in range(request.repair_attempts + 1):
            try:
                validated = self._validate_structured(request, content)
                if self._estimate(validated) > request.max_output_tokens:
                    raise ProviderFailure("AI_OUTPUT_LIMIT_EXCEEDED", retryable=False)
                return validated, AIUsage(repair_input, repair_output), repair_cost
            except ProviderFailure:
                raise
            except (json.JSONDecodeError, ValueError):
                if repair_attempt >= request.repair_attempts:
                    repair_usage = (
                        AIUsage(repair_input, repair_output)
                        if repair_input or repair_output
                        else None
                    )
                    raise ProviderFailure(
                        "AI_INVALID_RESPONSE", retryable=False, usage=repair_usage
                    ) from None
                repair_prompt = (
                    f"<repair schema='{request.output_schema_ref}' error='schema-validation'>"
                    "Return only one conforming JSON value.</repair>"
                )
                repair_envelope = self._model(candidate.model_key).estimate_cost(
                    self._estimate(repair_prompt), request.max_output_tokens
                )
                if repair_envelope > remaining_cost - repair_cost:
                    raise ProviderFailure("AI_REPAIR_BUDGET_EXHAUSTED", retryable=False) from None
                repair_number = provider_attempt * 100 + repair_attempt + 1
                effect_key = f"{invocation.invocation_id}:repair:{repair_number}"
                repaired = await self.store.load_provider_effect(
                    invocation.invocation_id, effect_key=effect_key
                )
                if repaired is None:
                    await self.store.reserve_provider_effect(
                        invocation.invocation_id,
                        effect_key=effect_key,
                        attempt_number=repair_number,
                        model_key=candidate.model_key,
                        owner=owner,
                        generation=generation,
                    )
                    repair_call = adapter.invoke(
                        model_key=candidate.model_key,
                        prompt=repair_prompt,
                        request=request,
                        effect_key=effect_key,
                    )
                    if request.deadline is None:
                        repaired = await repair_call
                    else:
                        remaining = (request.deadline - self._clock.now()).total_seconds()
                        try:
                            repaired = await asyncio.wait_for(
                                repair_call, timeout=max(0, remaining)
                            )
                        except TimeoutError:
                            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=False) from None
                    await self._fence(
                        invocation.invocation_id,
                        owner,
                        generation,
                        ownership_lost,
                    )
                    await self.store.record_provider_effect(
                        invocation.invocation_id,
                        effect_key=effect_key,
                        result=repaired,
                        owner=owner,
                        generation=generation,
                    )
                await self._fence(invocation.invocation_id, owner, generation, ownership_lost)
                usage = repaired.usage or AIUsage(
                    self._estimate(repair_prompt), request.max_output_tokens, estimated=True
                )
                if usage.output_tokens > request.max_output_tokens:
                    raise ProviderFailure(
                        "AI_OUTPUT_LIMIT_EXCEEDED", retryable=False, usage=usage
                    ) from None
                cost = self._usage_cost(candidate.model_key, usage)
                if cost > remaining_cost - repair_cost:
                    raise ProviderFailure(
                        "AI_REPAIR_BUDGET_EXHAUSTED", retryable=False, usage=usage
                    ) from None
                repair_input += usage.input_tokens
                repair_output += usage.output_tokens
                repair_cost += cost
                await self.store.record_attempt(
                    invocation.invocation_id,
                    attempt_number=repair_number,
                    model_key=candidate.model_key,
                    state="repair",
                    usage=usage,
                    cost=cost,
                )
                self._observe(
                    invocation,
                    "ai.structured.repair",
                    {
                        "provider_attempt": provider_attempt,
                        "repair_attempt": repair_attempt + 1,
                        "schema_ref": request.output_schema_ref or "missing",
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cost": str(cost),
                    },
                )
                content = repaired.content
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_structured(request: AIInvocationRequest, content: str) -> str:
        parsed: object = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("structured output must be an object")
        value = cast(dict[str, object], parsed)
        if request.output_schema_ref in {"answer-v1", "reference-answer-v1"}:
            if set(value) - {"answer", "model"}:
                raise ValueError("structured output contains unsupported properties")
            if not isinstance(value.get("answer"), str) or not value["answer"]:
                raise ValueError("structured output requires a non-empty answer")
            if "model" in value and not isinstance(value["model"], str):
                raise ValueError("structured output model must be a string")
        elif request.output_schema_ref == "analysis-v1":
            if set(value) != {"result"} or not isinstance(value["result"], dict):
                raise ValueError("structured output requires result object")
            result = cast(dict[str, object], value["result"])
            if set(result) != {"summary", "items"}:
                raise ValueError("structured result shape is invalid")
            if not isinstance(result["summary"], str) or not result["summary"]:
                raise ValueError("structured summary must be a non-empty string")
            raw_items = result["items"]
            if not isinstance(raw_items, list):
                raise ValueError("structured items must be strings")
            items = cast(list[object], raw_items)
            if not all(isinstance(item, str) for item in items):
                raise ValueError("structured items must be strings")
        elif request.output_schema is not None:
            ReferenceAIGateway._validate_json_schema(request.output_schema, value)
        else:
            raise ValueError("structured output schema is unresolved")
        return json.dumps(value, sort_keys=True)

    @staticmethod
    def _validate_json_schema(schema: Mapping[str, object], value: object) -> None:
        """Small provider-neutral JSON-Schema subset used by governed packages.

        Schema semantics remain owned by Prompt Pipeline; this routine merely
        interprets the exact resolved schema at the Gateway validation boundary.
        """
        schema_type = schema.get("type")
        if schema_type == "object":
            if not isinstance(value, dict):
                raise ValueError("structured output must be an object")
            object_value = cast(dict[str, object], value)
            properties = schema.get("properties")
            required = schema.get("required")
            # Prompt Pipeline deep-freezes governed package content, so an
            # immutable schema's JSON-array members are tuples at this boundary.
            if not isinstance(properties, Mapping) or not isinstance(required, list | tuple):
                raise ValueError("governed object schema is invalid")
            property_mapping = cast(Mapping[str, object], properties)
            required_fields = cast(list[object] | tuple[object, ...], required)
            if schema.get("additionalProperties") is False and set(object_value) - set(
                property_mapping
            ):
                raise ValueError("structured output contains unsupported properties")
            if not all(isinstance(key, str) and key in object_value for key in required_fields):
                raise ValueError("structured output omits a required property")
            for key, item in property_mapping.items():
                if key in object_value:
                    if not isinstance(item, Mapping):
                        raise ValueError("governed object schema is invalid")
                    ReferenceAIGateway._validate_json_schema(
                        cast(Mapping[str, object], item), object_value[key]
                    )
            return
        if schema_type == "string":
            if not isinstance(value, str):
                raise ValueError("structured output value must be a string")
            enum = schema.get("enum")
            if enum is not None and (not isinstance(enum, list | tuple) or value not in enum):
                raise ValueError("structured output value is outside the governed enum")
            return
        if schema_type == "array":
            if not isinstance(value, list):
                raise ValueError("structured output value must be an array")
            items = schema.get("items")
            if not isinstance(items, Mapping):
                raise ValueError("governed array schema is invalid")
            array_value = cast(list[object], value)
            for item in array_value:
                ReferenceAIGateway._validate_json_schema(cast(Mapping[str, object], items), item)
            return
        raise ValueError("governed structured output schema is unsupported")

    def _transition(self, invocation: GatewayInvocation, state: InvocationState) -> None:
        invocation.state = state
        self.lifecycle[invocation.invocation_id].append(state)
        self._observe(invocation, "ai.invocation.transition", {"state": state.value})

    def _observe(
        self, invocation: GatewayInvocation, operation: str, attributes: Mapping[str, object]
    ) -> None:
        """Record scoped, provider-neutral evidence without prompt or response content."""
        self._observations.record_log(
            context=ObservabilityContext(
                component_identity="AI Gateway",
                operation_name=operation,
                contract_version="1.0",
                observed_at=self._clock.now(),
                environment_identity="reference",
                deployment_identity="offline",
                data_classification=invocation.request.data_classification,
                redaction_status=RedactionStatus.APPLIED,
                tenant_id=invocation.request.tenant_id,
                workspace_id=invocation.request.workspace_id,
                correlation_id=invocation.request.correlation_id,
                causation_id=invocation.request.causation_id,
                command_id=invocation.request.command_id,
                execution_id=invocation.request.execution_id,
                ai_invocation_id=invocation.invocation_id,
            ),
            severity=LogSeverity.INFO,
            message="AI Gateway conformance evidence",
            attributes=attributes,
        )

    def _success(
        self,
        invocation: GatewayInvocation,
        content: str,
        usage: AIUsage,
        route: RouteDecision,
        *,
        cache_hit: bool = False,
    ) -> AIInvocationResponse:
        self._transition(invocation, InvocationState.SUCCEEDED)
        result = self._outcomes.succeeded(
            subject=invocation.invocation_id,
            producer="AI Gateway",
            tenant_id=invocation.request.tenant_id,
            workspace_id=invocation.request.workspace_id,
            correlation_id=invocation.request.correlation_id,
            causation_id=invocation.request.causation_id,
            value_reference=f"ai-content:{hashlib.sha256(content.encode()).hexdigest()}",
            metadata={
                "gateway_accounting_evidence": {
                    "evidence_version": 1,
                    "status": "settled",
                    "tenant_id": invocation.request.tenant_id,
                    "workspace_id": invocation.request.workspace_id,
                    "ai_invocation_id": invocation.invocation_id,
                    "settled_result_id": "pending_result_binding",
                    "actual_cost": format(invocation.cumulative_cost, "f").rstrip("0").rstrip("."),
                    "currency_or_reference_unit": "USD",
                }
            },
        )
        gateway_evidence = cast(dict[str, object], result.metadata["gateway_accounting_evidence"])
        gateway_evidence["settled_result_id"] = result.result_id
        response = AIInvocationResponse(
            invocation.invocation_id, result, content, usage=usage, route=route, cache_hit=cache_hit
        )
        invocation.terminal = response
        self._observe(
            invocation,
            "ai.invocation.succeeded",
            {
                "result_id": result.result_id,
                "capability_id": invocation.request.capability_id,
                "capability_contract_version_id": (
                    invocation.request.capability_contract_version_id
                ),
                "prompt_package_ref": invocation.request.prompt_template_ref,
                "prompt_package_version_ref": invocation.request.prompt_template_version_ref,
                "disposition": "invoked",
                "cache_hit": cache_hit,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cached_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "token_measurement_status": "estimated" if usage.estimated else "measured",
                "estimated_cost": str(route.estimated_cost),
                "actual_cost_status": "canonical_store",
                "budget_outcome": "within_ceiling",
                "provider_attempt_count_status": "canonical_store",
                "repair_attempt_count_status": "canonical_store",
                "fallback_attempt_count_status": "canonical_store",
                "total_model_call_count_status": "canonical_store",
                "model_key": route.model_key,
            },
        )
        return response

    def _failure(
        self, invocation: GatewayInvocation, failure: ProviderFailure
    ) -> AIInvocationResponse:
        status = ResultStatus.FAILED
        state = InvocationState.FAILED
        category = ErrorCategory.AI_PROVIDER_UNAVAILABLE
        if "TIMEOUT" in failure.code:
            status = ResultStatus.TIMED_OUT
            state = InvocationState.TIMED_OUT
            category = ErrorCategory.TIMEOUT
        elif "CANCELLED" in failure.code:
            status = ResultStatus.CANCELLED
            state = InvocationState.CANCELLED
            category = ErrorCategory.CANCELLATION
        elif not failure.retryable:
            category = ErrorCategory.AI_INVALID_RESPONSE
        self._transition(invocation, state)
        result, error = self._outcomes.unsuccessful(
            status=status,
            subject=invocation.invocation_id,
            producer="AI Gateway",
            tenant_id=invocation.request.tenant_id,
            workspace_id=invocation.request.workspace_id,
            correlation_id=invocation.request.correlation_id,
            causation_id=invocation.request.causation_id,
            error_code=failure.code,
            category=category,
            severity=ErrorSeverity.WARNING if failure.retryable else ErrorSeverity.ERROR,
            retry=RetryClassification.RETRYABLE
            if failure.retryable
            else RetryClassification.NEVER_RETRY,
            message="The provider-neutral mock invocation failed.",
        )
        response = AIInvocationResponse(
            invocation.invocation_id, result, error=error, route=invocation.route
        )
        invocation.terminal = response
        self._observe(
            invocation,
            "ai.invocation.failed",
            {
                "result_id": result.result_id,
                "capability_id": invocation.request.capability_id,
                "capability_contract_version_id": (
                    invocation.request.capability_contract_version_id
                ),
                "prompt_package_ref": invocation.request.prompt_template_ref,
                "prompt_package_version_ref": invocation.request.prompt_template_version_ref,
                "disposition": "invoked",
                "error_id": error.error_id,
                "error_code": failure.code,
                "retryable": failure.retryable,
                "status": status.value,
            },
        )
        return response


__all__ = (
    "AIGateway",
    "AIInvocationRequest",
    "AIInvocationResponse",
    "AIUsage",
    "Acceptance",
    "CachedContent",
    "ContextItem",
    "GatewayInvocation",
    "GatewayReservation",
    "InvocationState",
    "ModelCatalogEntry",
    "ProviderAdapter",
    "ProviderFailure",
    "ProviderResult",
    "ProviderStreamEvent",
    "ReferenceAIGateway",
    "ReferenceGatewayStore",
    "ResponseMode",
    "RouteDecision",
    "StreamChunk",
)
