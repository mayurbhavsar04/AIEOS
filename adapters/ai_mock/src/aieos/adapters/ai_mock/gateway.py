"""Deterministic AI Gateway adapter with no external provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import StrEnum

from aieos.ai_gateway import (
    AIInvocationRequest,
    AIInvocationResponse,
    AIUsage,
    ProviderEffectBoundary,
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
        effect_boundary: ProviderEffectBoundary | None = None,
    ) -> None:
        self.key = key
        self.prefix = prefix
        self.transient_failures = transient_failures
        self.calls = 0
        self.prompts: list[str] = []
        self._behaviors = list(behaviors)
        self._effects: dict[str, ProviderResult | ProviderFailure] = {}
        self._effect_boundary = effect_boundary

    def _behavior(self) -> MockProviderBehavior:
        return self._behaviors.pop(0) if self._behaviors else MockProviderBehavior.SUCCESS

    @property
    def effect_cache_size(self) -> int:
        """Expose process-local replay state for fresh-adapter durability assertions."""
        return len(self._effects)

    def use_effect_boundary(self, boundary: ProviderEffectBoundary) -> None:
        """Attach the provider-owned durable boundary during composition."""
        self._effect_boundary = boundary

    async def invoke(
        self,
        *,
        model_key: str,
        prompt: str,
        request: AIInvocationRequest,
        effect_key: str | None = None,
    ) -> ProviderResult:
        if effect_key is not None and self._effect_boundary is not None:
            request_hash = hashlib.sha256(
                json.dumps(
                    {
                        "model_key": model_key,
                        "prompt": prompt,
                        "tenant_id": request.tenant_id,
                        "workspace_id": request.workspace_id,
                        "response_mode": request.response_mode.value,
                        "output_schema_ref": request.output_schema_ref,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            return await self._effect_boundary.execute(
                request=request,
                effect_key=effect_key,
                effect_type=(
                    "structured_repair" if ":repair:" in effect_key else "provider_invoke"
                ),
                request_hash=request_hash,
                operation=lambda: self._invoke_once(
                    model_key=model_key,
                    prompt=prompt,
                    request=request,
                    effect_key=None,
                ),
            )
        return await self._invoke_once(
            model_key=model_key,
            prompt=prompt,
            request=request,
            effect_key=effect_key,
        )

    async def _invoke_once(
        self,
        *,
        model_key: str,
        prompt: str,
        request: AIInvocationRequest,
        effect_key: str | None,
    ) -> ProviderResult:
        if effect_key is not None and effect_key in self._effects:
            replay = self._effects[effect_key]
            if isinstance(replay, ProviderFailure):
                raise replay
            return replay
        self.calls += 1
        self.prompts.append(prompt)
        failure_usage = AIUsage(input_tokens=max(1, len(prompt.encode()) // 4), output_tokens=0)
        if self.transient_failures:
            self.transient_failures -= 1
            failure = ProviderFailure(
                "AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True, usage=failure_usage
            )
            if effect_key is not None:
                self._effects[effect_key] = failure
            raise failure
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
        elif request.output_schema_ref == "structured-task-kind-schema-v1":
            normalized = request.prompt.strip()
            first = normalized.split(maxsplit=1)[0].lower().rstrip(".,!?")
            if normalized.endswith("?"):
                kind = "Question"
            elif first in {
                "add",
                "apply",
                "avoid",
                "build",
                "check",
                "count",
                "create",
                "delete",
                "do",
                "fail",
                "keep",
                "leave",
                "limit",
                "list",
                "measure",
                "normalize",
                "open",
                "preserve",
                "propagate",
                "record",
                "reject",
                "remove",
                "report",
                "resolve",
                "return",
                "run",
                "select",
                "send",
                "stop",
                "update",
                "use",
                "validate",
                "verify",
                "write",
            }:
                kind = "Instruction"
            else:
                kind = "Statement"
            content = json.dumps({"task_kind": kind})
        elif request.output_schema_ref == "analysis-v1":
            content = json.dumps(
                {"result": {"summary": request.prompt.strip(), "items": [model_key]}}
            )
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
        result = ProviderResult(content, usage, confidence=confidence)
        if effect_key is not None:
            self._effects[effect_key] = result
        return result

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
            if behavior is MockProviderBehavior.MID_STREAM_FAILURE and index == 0:
                yield ProviderStreamEvent(
                    "usage",
                    usage=AIUsage(
                        input_tokens=max(1, len(prompt.encode()) // 4),
                        output_tokens=max(1, len((word + " ").encode()) // 4),
                    ),
                )
        if behavior is not MockProviderBehavior.MISSING_USAGE:
            yield ProviderStreamEvent(
                "usage",
                usage=AIUsage(
                    input_tokens=max(1, len(prompt.encode()) // 4),
                    output_tokens=max(1, len(content.encode()) // 4),
                ),
            )


__all__ = ("DeterministicMockProvider", "MockAIGateway", "MockProviderBehavior")
