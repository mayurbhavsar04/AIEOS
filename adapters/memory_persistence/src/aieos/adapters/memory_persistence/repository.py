"""In-memory Memory repository with immutable identity enforcement."""

from aieos.memory_service import MemoryRecord


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}

    def save(self, record: MemoryRecord) -> None:
        existing = self.records.get(record.memory_id)
        if existing is not None and existing != record:
            raise ValueError("MemoryId cannot be reused")
        self.records[record.memory_id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self.records.get(memory_id)


__all__ = ("InMemoryMemoryRepository",)
