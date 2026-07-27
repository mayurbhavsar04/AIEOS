"""Provider-neutral AI invocation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aieos.contracts import AuthorizationContext, ErrorEnvelope, ResultEnvelope


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


@dataclass(frozen=True, slots=True)
class AIInvocationResponse:
    ai_invocation_id: str
    result: ResultEnvelope
    content: str | None = None
    error: ErrorEnvelope | None = None


class AIGateway(Protocol):
    """Invoke one provider-independent AI operation."""

    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse: ...


__all__ = ("AIGateway", "AIInvocationRequest", "AIInvocationResponse")
