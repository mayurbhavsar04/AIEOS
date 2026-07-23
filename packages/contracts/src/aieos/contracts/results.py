"""Canonical immutable ES-007 Result and Error contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ResultStatus(StrEnum):
    ACCEPTED = "Accepted"
    IN_PROGRESS = "InProgress"
    SUCCEEDED = "Succeeded"
    PARTIALLY_SUCCEEDED = "PartiallySucceeded"
    REJECTED = "Rejected"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    TIMED_OUT = "TimedOut"


class OutcomeCategory(StrEnum):
    ACKNOWLEDGEMENT = "Acknowledgement"
    PROGRESS = "Progress"
    SUCCESS = "Success"
    PARTIAL_SUCCESS = "PartialSuccess"
    REJECTION = "Rejection"
    FAILURE = "Failure"
    CANCELLATION = "Cancellation"
    TIMEOUT = "Timeout"


STATUS_CATEGORY: dict[ResultStatus, OutcomeCategory] = {
    ResultStatus.ACCEPTED: OutcomeCategory.ACKNOWLEDGEMENT,
    ResultStatus.IN_PROGRESS: OutcomeCategory.PROGRESS,
    ResultStatus.SUCCEEDED: OutcomeCategory.SUCCESS,
    ResultStatus.PARTIALLY_SUCCEEDED: OutcomeCategory.PARTIAL_SUCCESS,
    ResultStatus.REJECTED: OutcomeCategory.REJECTION,
    ResultStatus.FAILED: OutcomeCategory.FAILURE,
    ResultStatus.CANCELLED: OutcomeCategory.CANCELLATION,
    ResultStatus.TIMED_OUT: OutcomeCategory.TIMEOUT,
}


class ErrorCategory(StrEnum):
    VALIDATION = "Validation"
    AUTHENTICATION = "Authentication"
    AUTHORIZATION = "Authorization"
    NOT_FOUND = "NotFound"
    CONFLICT = "Conflict"
    CONCURRENCY = "Concurrency"
    RATE_LIMIT = "RateLimit"
    QUOTA = "Quota"
    DEPENDENCY_UNAVAILABLE = "DependencyUnavailable"
    DEPENDENCY_FAILURE = "DependencyFailure"
    TIMEOUT = "Timeout"
    CANCELLATION = "Cancellation"
    POLICY_VIOLATION = "PolicyViolation"
    UNSUPPORTED_CAPABILITY = "UnsupportedCapability"
    CAPABILITY_COMPATIBILITY = "CapabilityCompatibility"
    MEMORY_READ = "MemoryRead"
    MEMORY_WRITE = "MemoryWrite"
    AI_PROVIDER_UNAVAILABLE = "AIProviderUnavailable"
    AI_PROVIDER_REJECTED = "AIProviderRejected"
    AI_CONTENT_POLICY = "AIContentPolicy"
    AI_INVALID_RESPONSE = "AIInvalidResponse"
    WORKFLOW_STATE = "WorkflowState"
    EXECUTION_FAILURE = "ExecutionFailure"
    INTERNAL_INVARIANT = "InternalInvariant"
    UNKNOWN = "Unknown"


class ErrorSeverity(StrEnum):
    INFORMATIONAL = "Informational"
    WARNING = "Warning"
    ERROR = "Error"
    CRITICAL = "Critical"


class RetryClassification(StrEnum):
    NEVER_RETRY = "NeverRetry"
    RETRYABLE = "Retryable"
    RETRYABLE_AFTER_DELAY = "RetryableAfterDelay"
    RETRYABLE_AFTER_CONDITION = "RetryableAfterCondition"
    REQUIRES_POLICY_EVALUATION = "RequiresPolicyEvaluation"


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    error_id: str
    error_code: str
    error_category: ErrorCategory
    error_severity: ErrorSeverity
    retry_classification: RetryClassification
    message: str
    originating_component: str
    affected_subject: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    causation_id: str
    occurred_at: datetime
    contract_version: str = "1.0"
    diagnostic_reference: str | None = None
    external_error_reference: str | None = None
    parent_error_id: str | None = None
    root_error_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())

    def __post_init__(self) -> None:
        if not all(
            (
                self.error_id,
                self.error_code,
                self.message,
                self.originating_component,
                self.affected_subject,
                self.tenant_id,
                self.workspace_id,
                self.correlation_id,
                self.causation_id,
            )
        ):
            raise ValueError("required Error fields must be non-empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Error occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ResultEnvelope:
    result_id: str
    result_status: ResultStatus
    outcome_category: OutcomeCategory
    subject_reference: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    causation_id: str
    producer_component: str
    contract_version: str = "1.0"
    command_id: str | None = None
    event_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    value_reference: str | None = None
    error_id: str | None = None
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())
    predecessor_result_id: str | None = None
    parent_result_id: str | None = None
    child_result_references: tuple[str, ...] = ()
    disposition_counts: Mapping[str, int] = field(default_factory=lambda: dict[str, int]())

    def __post_init__(self) -> None:
        if STATUS_CATEGORY[self.result_status] != self.outcome_category:
            raise ValueError("ResultStatus and OutcomeCategory are inconsistent")
        if (
            self.result_status
            in {
                ResultStatus.REJECTED,
                ResultStatus.FAILED,
                ResultStatus.CANCELLED,
                ResultStatus.TIMED_OUT,
            }
            and not self.error_id
        ):
            raise ValueError("unsuccessful terminal Results require ErrorId")
        if (
            self.result_status
            in {
                ResultStatus.SUCCEEDED,
                ResultStatus.PARTIALLY_SUCCEEDED,
                ResultStatus.REJECTED,
                ResultStatus.FAILED,
                ResultStatus.CANCELLED,
                ResultStatus.TIMED_OUT,
            }
            and self.completed_at is None
        ):
            raise ValueError("terminal Results require CompletedAt")
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise ValueError("Result completed_at must be timezone-aware")


__all__ = (
    "ErrorCategory",
    "ErrorEnvelope",
    "ErrorSeverity",
    "OutcomeCategory",
    "ResultEnvelope",
    "ResultStatus",
    "RetryClassification",
)
