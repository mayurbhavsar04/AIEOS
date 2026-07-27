"""Pure time and identity ports used by authoritative runtime owners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4


class Clock(Protocol):
    """Return timezone-aware UTC instants."""

    def now(self) -> datetime: ...


class IdentifierFactory(Protocol):
    """Create opaque identifiers with a stable diagnostic prefix."""

    def new(self, prefix: str) -> str: ...


class SystemClock:
    """Production clock implementation based on the standard library."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdentifierFactory:
    """Production opaque identifier factory."""

    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """Immutable evidence that may be referenced by a CausationId."""

    decision_id: str
    decision_type: str
    component: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    recorded_at: datetime
    triggering_id: str | None = None


class InMemoryDecisionEvidenceRepository:
    """Reference repository for resolvable decision causation."""

    def __init__(self) -> None:
        self.decisions: dict[str, DecisionEvidence] = {}

    def record(self, evidence: DecisionEvidence) -> None:
        existing = self.decisions.get(evidence.decision_id)
        if existing is not None and existing != evidence:
            raise ValueError("DecisionId cannot be reused with changed evidence")
        self.decisions[evidence.decision_id] = evidence

    def contains(self, decision_id: str) -> bool:
        return decision_id in self.decisions


__all__ = (
    "Clock",
    "DecisionEvidence",
    "IdentifierFactory",
    "InMemoryDecisionEvidenceRepository",
    "SystemClock",
    "UuidIdentifierFactory",
)
