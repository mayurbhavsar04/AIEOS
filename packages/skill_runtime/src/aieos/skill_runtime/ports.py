"""Skill Runtime public bootstrap port."""

from typing import Protocol


class ExecutionAttemptRunner(Protocol):
    """Execute exactly one requested attempt; no retry-decision operation exists."""

    async def execute_attempt(self, execution_id: str) -> None: ...


__all__ = ("ExecutionAttemptRunner",)
