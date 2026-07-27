"""Canonical ES-008 observability context and log record."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DataClassification(StrEnum):
    NON_SENSITIVE = "NonSensitive"
    INTERNAL = "Internal"
    RESTRICTED = "Restricted"


class RedactionStatus(StrEnum):
    NOT_REQUIRED = "NotRequired"
    APPLIED = "Applied"


class LogSeverity(StrEnum):
    TRACE = "Trace"
    DEBUG = "Debug"
    INFO = "Info"
    WARN = "Warn"
    ERROR = "Error"
    CRITICAL = "Critical"


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    component_identity: str
    operation_name: str
    contract_version: str
    observed_at: datetime
    environment_identity: str
    deployment_identity: str
    data_classification: DataClassification
    redaction_status: RedactionStatus
    tenant_id: str
    workspace_id: str
    correlation_id: str
    causation_id: str
    request_id: str | None = None
    command_id: str | None = None
    event_id: str | None = None
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    execution_id: str | None = None
    ai_invocation_id: str | None = None
    result_id: str | None = None
    error_id: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LogRecord:
    log_record_id: str
    context: ObservabilityContext
    severity: LogSeverity
    message: str
    attributes: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


__all__ = (
    "DataClassification",
    "LogRecord",
    "LogSeverity",
    "ObservabilityContext",
    "RedactionStatus",
)
