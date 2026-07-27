"""Safe in-memory observability adapter used by the reference workflow."""

from __future__ import annotations

from collections.abc import Mapping

from aieos.contracts import LogRecord, LogSeverity, ObservabilityContext
from aieos.domain import IdentifierFactory


class InMemoryObservationRecorder:
    """Capture immutable logs for deterministic assertions."""

    def __init__(self, identifiers: IdentifierFactory) -> None:
        self._identifiers = identifiers
        self.records: list[LogRecord] = []

    def record_log(
        self,
        *,
        context: ObservabilityContext,
        severity: LogSeverity,
        message: str,
        attributes: Mapping[str, object] | None = None,
    ) -> LogRecord:
        record = LogRecord(
            log_record_id=self._identifiers.new("log"),
            context=context,
            severity=severity,
            message=message,
            attributes=attributes or {},
        )
        self.records.append(record)
        return record


__all__ = ("InMemoryObservationRecorder",)
