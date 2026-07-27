"""Observability ports; telemetry never owns business behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from aieos.contracts import LogRecord, LogSeverity, ObservabilityContext


class ObservationRecorder(Protocol):
    """Record safe immutable observations."""

    def record_log(
        self,
        *,
        context: ObservabilityContext,
        severity: LogSeverity,
        message: str,
        attributes: Mapping[str, object] | None = None,
    ) -> LogRecord: ...


__all__ = ("ObservationRecorder",)
