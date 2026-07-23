"""Immutable Event contract aligned with frozen ES-005."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EventMetadata:
    """Versioned, non-authoritative delivery and trace context."""

    contract_version: str = "1.0"
    trace_id: str | None = None
    span_id: str | None = None
    attributes: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """One immutable fact with one authoritative producer."""

    event_id: str
    event_type: str
    event_version: str
    occurred_at: datetime
    recorded_at: datetime
    producer: str
    correlation_id: str
    subject: str
    payload: Mapping[str, object]
    metadata: EventMetadata
    causation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    request_id: str | None = None
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    execution_id: str | None = None
    ai_invocation_id: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.event_id,
            self.event_type,
            self.event_version,
            self.producer,
            self.correlation_id,
            self.subject,
        )
        if not all(required):
            raise ValueError("required Event fields must be non-empty")
        if self.occurred_at.tzinfo is None or self.recorded_at.tzinfo is None:
            raise ValueError("Event timestamps must be timezone-aware")
        if (self.tenant_id is None) != (self.workspace_id is None):
            raise ValueError("Tenant and Workspace scope must be present together")


__all__ = ("EventEnvelope", "EventMetadata")
