"""Deterministic AI Gateway adapter with no external provider."""

from __future__ import annotations

import asyncio

from aieos.ai_gateway import AIInvocationRequest, AIInvocationResponse
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


__all__ = ("MockAIGateway",)
