"""Deterministic AI Gateway adapter with no external provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal

from aieos.ai_gateway import (
    AIInvocationRequest,
    AIInvocationResponse,
    AIUsage,
    ProviderBehavior,
    ProviderFailure,
    ProviderResult,
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


class DeterministicMockProvider:
    """Configurable provider adapter that never performs network I/O."""

    def __init__(self, key: str, *, prefix: str, transient_failures: int = 0) -> None:
        self.key = key
        self.prefix = prefix
        self.transient_failures = transient_failures
        self.calls = 0

    async def invoke(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> ProviderResult:
        self.calls += 1
        if self.transient_failures:
            self.transient_failures -= 1
            raise ProviderFailure("AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True)
        behavior = request.behavior
        if behavior is ProviderBehavior.TRANSIENT_FAILURE:
            raise ProviderFailure("AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True)
        if behavior in {ProviderBehavior.PERMANENT_FAILURE, ProviderBehavior.POLICY_REJECTION}:
            raise ProviderFailure("AI_PROVIDER_REJECTED", retryable=False)
        if behavior is ProviderBehavior.TIMEOUT:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True)
        if behavior is ProviderBehavior.CANCELLED:
            raise ProviderFailure("AI_PROVIDER_CANCELLED", retryable=False)
        if behavior is ProviderBehavior.MALFORMED:
            content = "not-json"
        elif behavior is ProviderBehavior.STRUCTURED or request.output_schema_ref:
            content = json.dumps({"answer": request.prompt.strip(), "model": model_key})
        else:
            content = f"{self.prefix}: {request.prompt.strip()}"
        usage = None
        if behavior is not ProviderBehavior.MISSING_USAGE:
            usage = AIUsage(
                input_tokens=max(1, len(prompt.encode()) // 4),
                output_tokens=max(1, len(content.encode()) // 4),
            )
        confidence = (
            Decimal("0.25") if behavior is ProviderBehavior.LOW_CONFIDENCE else Decimal("1")
        )
        return ProviderResult(content, usage, confidence=confidence)

    async def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[str]:
        result = await self.invoke(model_key=model_key, prompt=prompt, request=request)
        for word in result.content.split():
            await asyncio.sleep(0)
            yield word + " "


__all__ = ("DeterministicMockProvider", "MockAIGateway")
