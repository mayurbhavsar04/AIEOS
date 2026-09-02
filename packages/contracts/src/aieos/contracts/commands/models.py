"""Immutable Command contract aligned with frozen ES-004."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from aieos.contracts.common import AuthorizationContext


@dataclass(frozen=True, slots=True)
class CommandMetadata:
    """Structured context that is never self-authenticating authority."""

    request_id: str
    idempotency_key: str
    authorization: AuthorizationContext
    attempt_number: int | None = None
    expires_at: datetime | None = None
    trace_id: str | None = None
    span_id: str | None = None
    skill_version_id: str | None = None
    authoritative_result_id: str | None = None
    workflow_ai_budget_admission: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.request_id or not self.idempotency_key:
            raise ValueError("request and idempotency identities are required")
        if self.attempt_number is not None and self.attempt_number < 1:
            raise ValueError("attempt number must be positive")
        if self.authoritative_result_id is not None and not self.authoritative_result_id.strip():
            raise ValueError("AuthoritativeResultId must be a non-empty opaque ResultId")
        if self.authoritative_result_id is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.authoritative_result_id
        ):
            raise ValueError("AuthoritativeResultId must be a well-formed opaque ResultId")


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """One directed request to exactly one accountable component."""

    command_id: str
    command_type: str
    command_version: str
    correlation_id: str
    causation_id: str
    target_component: str
    initiator: str
    timestamp: datetime
    tenant_id: str
    workspace_id: str
    payload: Mapping[str, object]
    metadata: CommandMetadata
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    execution_id: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.command_id,
            self.command_type,
            self.command_version,
            self.correlation_id,
            self.causation_id,
            self.target_component,
            self.initiator,
            self.tenant_id,
            self.workspace_id,
        )
        if not all(required):
            raise ValueError("required Command fields must be non-empty")
        if self.timestamp.tzinfo is None:
            raise ValueError("Command timestamp must be timezone-aware")
        if (
            self.metadata.authorization.tenant_id != self.tenant_id
            or self.metadata.authorization.workspace_id != self.workspace_id
        ):
            raise ValueError("Command scope must match verified authorization scope")
        if self.command_type == "DispatchExecutionAttempt":
            if self.command_version in {"2", "2.0"}:
                if self.metadata.skill_version_id is None or set(self.payload) != {"statement"}:
                    raise ValueError("DispatchExecutionAttempt v2 requires exact governed metadata")
            elif self.metadata.authoritative_result_id is not None:
                raise ValueError("AuthoritativeResultId is supported only by command v2")


__all__ = ("CommandEnvelope", "CommandMetadata")
