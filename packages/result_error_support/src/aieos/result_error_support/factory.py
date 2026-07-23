"""Result and Error constructors that preserve frozen status semantics."""

from __future__ import annotations

from collections.abc import Mapping

from aieos.contracts import (
    ErrorCategory,
    ErrorEnvelope,
    ErrorSeverity,
    OutcomeCategory,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.domain import Clock, IdentifierFactory


class OutcomeFactory:
    """Create normalized immutable outcomes without owning behavioral decisions."""

    def __init__(self, clock: Clock, identifiers: IdentifierFactory) -> None:
        self._clock = clock
        self._identifiers = identifiers

    def accepted(
        self,
        *,
        subject: str,
        producer: str,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str,
        causation_id: str,
        command_id: str,
        value_reference: str | None = None,
    ) -> ResultEnvelope:
        return ResultEnvelope(
            result_id=self._identifiers.new("result"),
            result_status=ResultStatus.ACCEPTED,
            outcome_category=OutcomeCategory.ACKNOWLEDGEMENT,
            subject_reference=subject,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            command_id=command_id,
            producer_component=producer,
            value_reference=value_reference,
        )

    def succeeded(
        self,
        *,
        subject: str,
        producer: str,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str,
        causation_id: str,
        command_id: str | None = None,
        event_id: str | None = None,
        value_reference: str | None = None,
        metadata: Mapping[str, object] | None = None,
        predecessor_result_id: str | None = None,
    ) -> ResultEnvelope:
        now = self._clock.now()
        return ResultEnvelope(
            result_id=self._identifiers.new("result"),
            result_status=ResultStatus.SUCCEEDED,
            outcome_category=OutcomeCategory.SUCCESS,
            subject_reference=subject,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            command_id=command_id,
            event_id=event_id,
            producer_component=producer,
            started_at=now,
            completed_at=now,
            value_reference=value_reference,
            metadata=metadata or {},
            predecessor_result_id=predecessor_result_id,
        )

    def unsuccessful(
        self,
        *,
        status: ResultStatus,
        subject: str,
        producer: str,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str,
        causation_id: str,
        error_code: str,
        category: ErrorCategory,
        severity: ErrorSeverity,
        retry: RetryClassification,
        message: str,
        command_id: str | None = None,
        event_id: str | None = None,
        predecessor_result_id: str | None = None,
    ) -> tuple[ResultEnvelope, ErrorEnvelope]:
        if status not in {
            ResultStatus.REJECTED,
            ResultStatus.FAILED,
            ResultStatus.CANCELLED,
            ResultStatus.TIMED_OUT,
        }:
            raise ValueError("unsuccessful factory requires unsuccessful terminal status")
        now = self._clock.now()
        error = ErrorEnvelope(
            error_id=self._identifiers.new("error"),
            error_code=error_code,
            error_category=category,
            error_severity=severity,
            retry_classification=retry,
            message=message,
            originating_component=producer,
            affected_subject=subject,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=now,
        )
        category_by_status = {
            ResultStatus.REJECTED: OutcomeCategory.REJECTION,
            ResultStatus.FAILED: OutcomeCategory.FAILURE,
            ResultStatus.CANCELLED: OutcomeCategory.CANCELLATION,
            ResultStatus.TIMED_OUT: OutcomeCategory.TIMEOUT,
        }
        result = ResultEnvelope(
            result_id=self._identifiers.new("result"),
            result_status=status,
            outcome_category=category_by_status[status],
            subject_reference=subject,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            command_id=command_id,
            event_id=event_id,
            producer_component=producer,
            started_at=now if status is not ResultStatus.REJECTED else None,
            completed_at=now,
            error_id=error.error_id,
            predecessor_result_id=predecessor_result_id,
        )
        return result, error


__all__ = ("OutcomeFactory",)
