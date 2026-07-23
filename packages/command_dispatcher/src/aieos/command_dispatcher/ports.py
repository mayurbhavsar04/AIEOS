"""Owned directed Command dispatch ports."""

from typing import Protocol

from aieos.contracts.commands import CommandMessage
from aieos.contracts.results import ResultEnvelope


class CommandHandler(Protocol):
    """Accountable target that owns authorization, idempotency, and execution."""

    async def handle(self, command: CommandMessage) -> ResultEnvelope: ...


class CommandDispatcher(Protocol):
    """Route one Command to one accountable target without business authority."""

    async def dispatch(self, command: CommandMessage) -> ResultEnvelope: ...


__all__ = ("CommandDispatcher", "CommandHandler")
