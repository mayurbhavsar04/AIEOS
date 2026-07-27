"""Scope-mandatory, append-safe durable Memory storage."""

from sqlalchemy import select

from aieos.memory_service import MemoryRecord

from .database import PostgresDatabase
from .models import MemoryRecordRow


class PostgresMemoryRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def save(self, record: MemoryRecord, *, version: int = 1) -> None:
        async with self._database.transaction() as session:
            session.add(
                MemoryRecordRow(
                    memory_id=record.memory_id,
                    version=version,
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    correlation_id=record.correlation_id,
                    provenance=record.provenance,
                    content=record.content,
                )
            )

    async def get(
        self, memory_id: str, *, tenant_id: str, workspace_id: str
    ) -> MemoryRecord | None:
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
