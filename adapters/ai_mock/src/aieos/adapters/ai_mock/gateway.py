"""Deterministic AI Gateway adapter with no external provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import StrEnum

from aieos.ai_gateway import (
    AIInvocationRequest,
    AIInvocationResponse,
    AIUsage,
    ProviderFailure,
    ProviderResult,
    ProviderStreamEvent,
)
from aieos.contracts import (
    ErrorCategory,
    ErrorSeverity,
    ResultStatus,
    RetryClassification,
)
from aieos.domain import Clock, IdentifierFactory
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import ScopeAuthorizer


class MockAIGateway:
    """Return deterministic content or configured provider-neutral failures."""

    def __init__(
        self,
        *,
        clock: Clock,
        identifiers: IdentifierFactory,
        authorizer: ScopeAuthorizer,
        failures_before_success: int = 0,
        delay_seconds: float = 0.0,
    ) -> None:
        self._clock = clock
        self._identifiers = identifiers
        self._authorizer = authorizer
        self._outcomes = OutcomeFactory(clock, identifiers)
        self._failures_remaining = failures_before_success
        self._delay_seconds = delay_seconds
        self.invocations: list[str] = []

    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse:
        self._authorizer.require(
            request.authorization,
            permission="ai.invoke",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
        )
        invocation_id = self._identifiers.new("ai")
        self.invocations.append(invocation_id)
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._failures_remaining:
            self._failures_remaining -= 1
            result, error = self._outcomes.unsuccessful(
                status=ResultStatus.FAILED,
                subject=invocation_id,
                producer="AI Gateway",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                error_code="AI_PROVIDER_TEMPORARILY_UNAVAILABLE",
                category=ErrorCategory.AI_PROVIDER_UNAVAILABLE,
                severity=ErrorSeverity.WARNING,
                retry=RetryClassification.RETRYABLE,
                message="The mock AI capability is temporarily unavailable.",
            )
            return AIInvocationResponse(invocation_id, result, error=error)
        result = self._outcomes.succeeded(
            subject=invocation_id,
            producer="AI Gateway",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            value_reference=f"value:{invocation_id}",
        )
        content = f"Hello from AIEOS: {request.prompt.strip()}"
        return AIInvocationResponse(invocation_id, result, content=content)


class MockProviderBehavior(StrEnum):
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    MALFORMED = "malformed"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_USAGE = "missing_usage"
    MID_STREAM_FAILURE = "mid_stream_failure"


class DeterministicMockProvider:
    """Configurable provider adapter that never performs network I/O."""

    def __init__(
        self,
        key: str,
        *,
        prefix: str,
        transient_failures: int = 0,
        behaviors: tuple[MockProviderBehavior, ...] = (),
    ) -> None:
        self.key = key
        self.prefix = prefix
        self.transient_failures = transient_failures
        self.calls = 0
        self.prompts: list[str] = []
        self._behaviors = list(behaviors)

    def _behavior(self) -> MockProviderBehavior:
        return self._behaviors.pop(0) if self._behaviors else MockProviderBehavior.SUCCESS

    async def invoke(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> ProviderResult:
        self.calls += 1
        self.prompts.append(prompt)
        failure_usage = AIUsage(input_tokens=max(1, len(prompt.encode()) // 4), output_tokens=0)
        if self.transient_failures:
            self.transient_failures -= 1
            raise ProviderFailure(
                "AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True, usage=failure_usage
            )
        behavior = self._behavior()
        if behavior is MockProviderBehavior.TRANSIENT_FAILURE:
            raise ProviderFailure(
                "AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True, usage=failure_usage
            )
        if behavior is MockProviderBehavior.PERMANENT_FAILURE:
            raise ProviderFailure("AI_PROVIDER_REJECTED", retryable=False, usage=failure_usage)
        if behavior is MockProviderBehavior.TIMEOUT:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True)
        if behavior is MockProviderBehavior.CANCELLED:
            raise ProviderFailure("AI_PROVIDER_CANCELLED", retryable=False)
        if behavior is MockProviderBehavior.MALFORMED:
            content = "not-json"
        elif request.output_schema_ref:
            content = json.dumps({"answer": request.prompt.strip(), "model": model_key})
        else:
            content = f"{self.prefix}: {request.prompt.strip()}"
        usage = None
        if behavior is not MockProviderBehavior.MISSING_USAGE:
            usage = AIUsage(
                input_tokens=max(1, len(prompt.encode()) // 4),
                output_tokens=max(1, len(content.encode()) // 4),
            )
        confidence = (
            Decimal("0.25") if behavior is MockProviderBehavior.LOW_CONFIDENCE else Decimal("1")
        )
        return ProviderResult(content, usage, confidence=confidence)

    async def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[ProviderStreamEvent]:
        self.calls += 1
        self.prompts.append(prompt)
        behavior = self._behavior()
        content = f"{self.prefix}: {request.prompt.strip()}"
        for index, word in enumerate(content.split()):
            await asyncio.sleep(0)
            if behavior is MockProviderBehavior.MID_STREAM_FAILURE and index == 1:
                raise ProviderFailure("AI_PROVIDER_STREAM_FAILED", retryable=False)
            yield ProviderStreamEvent("content_delta", content=word + " ")
        if behavior is not MockProviderBehavior.MISSING_USAGE:
            yield ProviderStreamEvent(
                "usage",
                usage=AIUsage(
                    input_tokens=max(1, len(prompt.encode()) // 4),
                    output_tokens=max(1, len(content.encode()) // 4),
                ),
            )


__all__ = ("DeterministicMockProvider", "MockAIGateway", "MockProviderBehavior")
