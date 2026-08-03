"""Provider-neutral AI Gateway contracts and deterministic reference runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

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


class ProviderBehavior(StrEnum):
    SUCCESS = "success"
    STRUCTURED = "structured"
    STREAM = "stream"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    MALFORMED = "malformed"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_USAGE = "missing_usage"
    POLICY_REJECTION = "policy_rejection"


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
    residency: str = "any"
    allow_fallback: bool = True
    max_provider_attempts: int = 2
    repair_attempts: int = 1
    behavior: ProviderBehavior = ProviderBehavior.SUCCESS


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
    healthy: bool = True
    deprecated: bool = False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return input_tokens * self.input_cost_per_token + output_tokens * self.output_cost_per_token


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    usage: AIUsage | None
    confidence: Decimal = Decimal("1")


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ProviderAdapter(Protocol):
    key: str

    async def invoke(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> ProviderResult: ...

    def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[str]: ...


class AIGateway(Protocol):
    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse: ...


@dataclass(slots=True)
class _Invocation:
    request: AIInvocationRequest
    invocation_id: str
    acknowledgement: ResultEnvelope
    state: InvocationState
    accepted_at: datetime
    route: RouteDecision | None = None
    terminal: AIInvocationResponse | None = None


@dataclass(slots=True)
class _Reservation:
    invocation_id: str
    tenant_id: str
    workspace_id: str
    amount: Decimal
    state: str
    expires_at: datetime
    actual: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _CachedContent:
    content: str
    usage: AIUsage
    expires_at: datetime
    provenance: str


class ReferenceGatewayStore:
    """Atomic reference state; ports can later receive a PostgreSQL adapter."""

    def __init__(self, *, tenant_limit: Decimal = Decimal("100")) -> None:
        self._lock = asyncio.Lock()
        self.invocations: dict[str, _Invocation] = {}
        self.admission: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.reservations: dict[str, _Reservation] = {}
        self.cache: dict[tuple[str, str, str], _CachedContent] = {}
        self.usage: dict[str, AIUsage] = {}
        self.tenant_limit = tenant_limit

    @staticmethod
    def request_digest(request: AIInvocationRequest) -> str:
        value = {
            "capability": request.capability_contract_version_id,
            "prompt": request.prompt,
            "mode": request.response_mode,
            "schema": request.output_schema_ref,
            "scope": (request.tenant_id, request.workspace_id),
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
            self.invocations[invocation_id] = _Invocation(
                request, invocation_id, acknowledgement, InvocationState.REQUESTED, now
            )
            return Acceptance(invocation_id, acknowledgement)

    async def reserve(
        self,
        invocation_id: str,
        tenant_id: str,
        workspace_id: str,
        amount: Decimal,
        *,
        now: datetime,
    ) -> _Reservation:
        async with self._lock:
            current = self.reservations.get(invocation_id)
            if current is not None:
                return current
            used = sum(
                reservation.amount
                for reservation in self.reservations.values()
                if reservation.state in {"pending", "committed"}
                and reservation.tenant_id == tenant_id
                and reservation.workspace_id == workspace_id
            )
            if used + amount > self.tenant_limit:
                raise ValueError("hard budget exceeded")
            reservation = _Reservation(
                invocation_id,
                tenant_id,
                workspace_id,
                amount,
                "pending",
                now + timedelta(minutes=5),
            )
            self.reservations[invocation_id] = reservation
            return reservation

    async def reconcile(self, invocation_id: str, actual: Decimal, usage: AIUsage) -> None:
        async with self._lock:
            reservation = self.reservations[invocation_id]
            reservation.state = "committed"
            reservation.actual = actual
            self.usage[invocation_id] = usage


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

    @property
    def observations(self) -> ObservationRecorder:
        """Expose the safe recorder for reference conformance inspection."""
        return self._observations

    async def accept(self, request: AIInvocationRequest) -> Acceptance:
        self._preflight(request)
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
        invocation = self.store.invocations[accepted.ai_invocation_id]
        if invocation.terminal is not None:
            return invocation.terminal
        return await self.execute(accepted.ai_invocation_id)

    async def execute(self, invocation_id: str) -> AIInvocationResponse:
        invocation = self.store.invocations[invocation_id]
        request = invocation.request
        self._transition(invocation, InvocationState.POLICY_VALIDATED)
        prompt, tokens, context_digest = self._assemble(request)
        cache_key = self._cache_key(request, context_digest)
        cached = self.store.cache.get((request.tenant_id, request.workspace_id, cache_key))
        route = self._route(request, tokens)
        invocation.route = route
        self._transition(invocation, InvocationState.PROVIDER_SELECTED)
        reservation = await self.store.reserve(
            invocation_id,
            request.tenant_id,
            request.workspace_id,
            route.estimated_cost,
            now=self._clock.now(),
        )
        self._transition(invocation, InvocationState.PREPARED)
        if cached is not None and cached.expires_at > self._clock.now():
            usage = replace(cached.usage, cached_tokens=cached.usage.input_tokens)
            await self.store.reconcile(invocation_id, Decimal("0"), usage)
            return self._success(invocation, cached.content, usage, route, cache_hit=True)
        candidates = [route]
        if request.allow_fallback:
            candidates.extend(
                candidate
                for candidate in self._eligible_routes(request, tokens)
                if candidate.model_key != route.model_key
            )
        last_failure: ProviderFailure | None = None
        for attempts, candidate in enumerate(candidates[: request.max_provider_attempts], start=1):
            if attempts > 1:
                self._transition(invocation, InvocationState.RETRYING)
            self._transition(invocation, InvocationState.INVOKED)
            adapter = self._adapters[candidate.adapter_key]
            try:
                provider_result = await adapter.invoke(
                    model_key=candidate.model_key, prompt=prompt, request=request
                )
                content = self._validate_and_repair(request, provider_result.content)
                if provider_result.confidence < Decimal("0.5"):
                    last_failure = ProviderFailure("AI_LOW_CONFIDENCE", retryable=True)
                    continue
                usage = provider_result.usage or AIUsage(
                    tokens, request.max_output_tokens, estimated=True
                )
                actual = self._model(candidate.model_key).estimate_cost(
                    usage.input_tokens, usage.output_tokens
                )
                await self.store.reconcile(invocation_id, actual, usage)
                self.store.cache[(request.tenant_id, request.workspace_id, cache_key)] = (
                    _CachedContent(
                        content, usage, self._clock.now() + timedelta(minutes=10), invocation_id
                    )
                )
                return self._success(invocation, content, usage, candidate)
            except ProviderFailure as failure:
                last_failure = failure
                if not failure.retryable:
                    break
        reservation.state = "released"
        return self._failure(
            invocation, last_failure or ProviderFailure("unknown", retryable=False)
        )

    async def stream(self, request: AIInvocationRequest) -> AsyncIterator[StreamChunk]:
        accepted = await self.accept(replace(request, response_mode=ResponseMode.STREAM))
        yield StreamChunk(accepted.ai_invocation_id, 0, "acknowledgement")
        response = await self.execute(accepted.ai_invocation_id)
        if response.content is not None:
            yield StreamChunk(accepted.ai_invocation_id, 1, "stream_start")
            for index, word in enumerate(response.content.split(), start=2):
                yield StreamChunk(accepted.ai_invocation_id, index, "content_delta", word + " ")
            yield StreamChunk(
                accepted.ai_invocation_id,
                len(response.content.split()) + 2,
                "terminal",
                usage=response.usage,
                terminal=response,
            )
        else:
            yield StreamChunk(accepted.ai_invocation_id, 1, "terminal", terminal=response)

    def _preflight(self, request: AIInvocationRequest) -> None:
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

    def _assemble(self, request: AIInvocationRequest) -> tuple[str, int, str]:
        unique: dict[tuple[str, str], ContextItem] = {}
        for item in sorted(
            request.context_items,
            key=lambda value: (-value.mandatory, -value.relevance, value.reference),
        ):
            unique.setdefault((item.reference, item.version), item)
        selected: list[ContextItem] = []
        used = self._estimate(request.prompt) + request.max_output_tokens
        for item in unique.values():
            cost = self._estimate(item.content)
            if item.mandatory or used + cost <= request.max_input_tokens:
                selected.append(item)
                used += cost
        sections = [
            f"<system ref='{request.system_instruction_ref}'>approved instructions</system>",
            f"<task>{request.prompt.strip()}</task>",
        ]
        sections.extend(
            "<evidence "
            f"ref='{item.reference}' trusted='{str(item.trusted).lower()}' "
            f"reason='{item.necessity_reason}'>{item.content}</evidence>"
            for item in selected
        )
        prompt = "\n".join(sections)
        digest = hashlib.sha256(
            "|".join(f"{item.reference}:{item.version}" for item in selected).encode()
        ).hexdigest()
        return prompt, self._estimate(prompt), digest

    @staticmethod
    def _estimate(text: str) -> int:
        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    def _eligible_routes(
        self, request: AIInvocationRequest, input_tokens: int
    ) -> list[RouteDecision]:
        decisions: list[RouteDecision] = []
        excluded: dict[str, str] = {}
        for entry in self._catalog:
            reason = None
            if entry.deprecated or not entry.healthy:
                reason = "unavailable"
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
            elif request.residency != "any" and request.residency not in entry.residencies:
                reason = "residency"
            cost = entry.estimate_cost(input_tokens, request.max_output_tokens)
            if reason is None and cost > request.max_total_cost:
                reason = "budget"
            if reason is not None:
                excluded[entry.model_key] = reason
                continue
            decision_hash = hashlib.sha256(
                (entry.model_key + str(input_tokens)).encode()
            ).hexdigest()[:12]
            decisions.append(
                RouteDecision(
                    decision_reference=f"route:{decision_hash}",
                    model_key=entry.model_key,
                    adapter_key=entry.adapter_key,
                    considered=tuple(sorted(model.model_key for model in self._catalog)),
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

    @staticmethod
    def _cache_key(request: AIInvocationRequest, context_digest: str) -> str:
        values = (
            request.prompt_template_version_ref,
            request.prompt,
            ",".join(sorted(request.required_capabilities)),
            context_digest,
            request.output_schema_ref or "none",
            request.safety_policy_ref,
            request.locale,
            str(request.max_output_tokens),
        )
        return hashlib.sha256("|".join(values).encode()).hexdigest()

    @staticmethod
    def _validate_and_repair(request: AIInvocationRequest, content: str) -> str:
        if request.response_mode is not ResponseMode.STRUCTURED:
            return content[: request.max_output_tokens * 4]
        for attempt in range(request.repair_attempts + 1):
            try:
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError("structured output must be an object")
                return json.dumps(value, sort_keys=True)
            except (json.JSONDecodeError, ValueError):
                if attempt >= request.repair_attempts:
                    raise ProviderFailure("AI_INVALID_RESPONSE", retryable=False) from None
                content = json.dumps({"repaired": content[:64]})
        raise AssertionError("unreachable")

    def _transition(self, invocation: _Invocation, state: InvocationState) -> None:
        invocation.state = state
        self.lifecycle[invocation.invocation_id].append(state)
        self._observations.record_log(
            context=ObservabilityContext(
                component_identity="AI Gateway",
                operation_name="ai.invocation.transition",
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
            message="AI invocation lifecycle transition",
            attributes={"state": state.value},
        )

    def _success(
        self,
        invocation: _Invocation,
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
        )
        response = AIInvocationResponse(
            invocation.invocation_id, result, content, usage=usage, route=route, cache_hit=cache_hit
        )
        invocation.terminal = response
        return response

    def _failure(self, invocation: _Invocation, failure: ProviderFailure) -> AIInvocationResponse:
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
        return response


__all__ = (
    "AIGateway",
    "AIInvocationRequest",
    "AIInvocationResponse",
    "AIUsage",
    "Acceptance",
    "ContextItem",
    "InvocationState",
    "ModelCatalogEntry",
    "ProviderAdapter",
    "ProviderBehavior",
    "ProviderFailure",
    "ProviderResult",
    "ReferenceAIGateway",
    "ReferenceGatewayStore",
    "ResponseMode",
    "RouteDecision",
    "StreamChunk",
)
