"""Scope-mandatory, transaction-participating durable Memory storage."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aieos.memory_service import MemoryRecord

from .database import PostgresDatabase
from .models import MemoryRecordRow


class PostgresMemoryRepository:
    """Stage Memory writes for the infrastructure-owned terminal checkpoint.

    ``MemoryService`` retains ownership of Memory semantics and identifiers.
    This adapter only coordinates persistence: ``save`` stages an immutable
    record, while ``flush_in_transaction`` persists it in the same transaction
    as execution state, target-owned idempotency, outcomes, and outbox events.
    """

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._pending: dict[tuple[str, str, str, int], MemoryRecord] = {}

    async def save(self, record: MemoryRecord, *, version: int = 1) -> None:
        key = (record.tenant_id, record.workspace_id, record.memory_id, version)
        existing = self._pending.get(key)
        if existing is not None and existing != record:
            raise ValueError("MemoryId and version cannot be reused with changed content")
        self._pending[key] = record

    async def prepare(self) -> None:
        """Satisfy the transaction-participant lifecycle without eager loading."""

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        """Persist staged records in the caller's atomic checkpoint."""
        for (tenant_id, workspace_id, memory_id, version), record in self._pending.items():
            statement = insert(MemoryRecordRow).values(
                memory_id=memory_id,
                version=version,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                correlation_id=record.correlation_id,
                provenance=record.provenance,
                content=record.content,
            )
            await session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=[
                        "tenant_id",
                        "workspace_id",
                        "memory_id",
                        "version",
                    ]
                )
            )
            stored = await session.get(
                MemoryRecordRow,
                (tenant_id, workspace_id, memory_id, version),
            )
            if (
                stored is None
                or stored.correlation_id != record.correlation_id
                or stored.provenance != record.provenance
                or stored.content != record.content
            ):
                raise ValueError("MemoryId and version cannot be reused with changed content")

    async def get(
        self, memory_id: str, *, tenant_id: str, workspace_id: str
    ) -> MemoryRecord | None:
        pending = tuple(
            (version, record)
            for (
                record_tenant,
                record_workspace,
                record_id,
                version,
            ), record in self._pending.items()
            if record_tenant == tenant_id
            and record_workspace == workspace_id
            and record_id == memory_id
        )
        if pending:
            return max(pending, key=lambda item: item[0])[1]
        statement = (
            select(MemoryRecordRow)
            .where(
                MemoryRecordRow.memory_id == memory_id,
                MemoryRecordRow.tenant_id == tenant_id,
                MemoryRecordRow.workspace_id == workspace_id,
            )
            .order_by(MemoryRecordRow.version.desc())
            .limit(1)
        )
        async with self._database.transaction() as session:
            row = await session.scalar(statement)
        if row is None:
            return None
        return MemoryRecord(
            memory_id=row.memory_id,
            content=row.content,
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
            correlation_id=row.correlation_id,
            provenance=row.provenance,
        )
