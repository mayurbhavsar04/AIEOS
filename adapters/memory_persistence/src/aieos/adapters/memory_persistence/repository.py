"""In-memory Memory repository with immutable identity enforcement."""

from aieos.memory_service import MemoryRecord


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}

    async def save(self, record: MemoryRecord) -> None:
        existing = self.records.get(record.memory_id)
        if existing is not None and existing != record:
            raise ValueError("MemoryId cannot be reused")
        self.records[record.memory_id] = record

    async def get(
        self, memory_id: str, *, tenant_id: str, workspace_id: str
    ) -> MemoryRecord | None:
        record = self.records.get(memory_id)
        if record is None:
            return None
        if record.tenant_id != tenant_id or record.workspace_id != workspace_id:
            return None
        return record


__all__ = ("InMemoryMemoryRepository",)
