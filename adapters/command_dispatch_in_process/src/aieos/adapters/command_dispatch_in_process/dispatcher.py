"""Routing-only in-process implementation of the Command Dispatcher."""

from __future__ import annotations

from aieos.command_dispatcher import CommandHandler
from aieos.contracts.commands import CommandEnvelope
from aieos.contracts.results import ResultEnvelope


class DuplicateCommandTarget(ValueError):
    """Raised when composition attempts ambiguous target registration."""


class UnknownCommandTarget(LookupError):
    """Raised when no accountable target is registered."""


class InProcessCommandDispatcher:
    """Validate routing metadata and invoke exactly one registered target."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, target_component: str, handler: CommandHandler) -> None:
        if not target_component:
            raise ValueError("target component must be non-empty")
        if target_component in self._handlers:
            raise DuplicateCommandTarget(target_component)
        self._handlers[target_component] = handler

    async def dispatch(self, command: CommandEnvelope) -> ResultEnvelope:
        if not command.target_component or not command.command_id or not command.command_version:
            raise ValueError("Command routing envelope is incomplete")
        try:
            handler = self._handlers[command.target_component]
        except KeyError as error:
            raise UnknownCommandTarget(command.target_component) from error
        return await handler.handle(command)


__all__ = ("DuplicateCommandTarget", "InProcessCommandDispatcher", "UnknownCommandTarget")
