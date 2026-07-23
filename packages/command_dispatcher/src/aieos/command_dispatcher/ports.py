"""Owned directed Command dispatch port."""

from typing import Protocol

from aieos.contracts.commands import CommandEnvelope


class CommandDispatcher(Protocol):
    """Route one Command to one accountable target without business authority."""

    async def dispatch(self, command: CommandEnvelope) -> None: ...


__all__ = ("CommandDispatcher",)
