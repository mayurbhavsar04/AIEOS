"""Memory ownership and access boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aieos.contracts import AuthorizationContext
from aieos.domain import Clock, IdentifierFactory
from aieos.security_support import ScopeAuthorizer


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    content: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    provenance: str
    authorization: AuthorizationContext


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    content: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    provenance: str


class MemoryRepository(Protocol):
    def save(self, record: MemoryRecord) -> None: ...

    def get(self, memory_id: str) -> MemoryRecord | None: ...


class MemoryService:
    """Own canonical Memory records and enforce exact scope."""

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        authorizer: ScopeAuthorizer,
        identifiers: IdentifierFactory,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._identifiers = identifiers
        self._clock = clock

    def store(self, write: MemoryWrite) -> MemoryRecord:
        self._authorizer.require(
            write.authorization,
            permission="memory.write",
            tenant_id=write.tenant_id,
            workspace_id=write.workspace_id,
        )
        _ = self._clock.now()
        record = MemoryRecord(
            memory_id=self._identifiers.new("memory"),
            content=write.content,
            tenant_id=write.tenant_id,
            workspace_id=write.workspace_id,
            correlation_id=write.correlation_id,
            provenance=write.provenance,
        )
        self._repository.save(record)
        return record

    def fetch(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        authorization: AuthorizationContext,
    ) -> MemoryRecord:
        self._authorizer.require(
            authorization,
            permission="memory.read",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        record = self._repository.get(memory_id)
        if record is None:
            raise LookupError(memory_id)
        if record.tenant_id != tenant_id or record.workspace_id != workspace_id:
            raise PermissionError("cross-scope Memory access denied")
        return record


__all__ = ("MemoryRecord", "MemoryRepository", "MemoryService", "MemoryWrite")
