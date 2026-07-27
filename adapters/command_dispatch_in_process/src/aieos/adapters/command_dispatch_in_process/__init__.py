"""In-process directed Command Dispatcher adapter."""

from aieos.adapters.command_dispatch_in_process.dispatcher import (
    DuplicateCommandTarget,
    InProcessCommandDispatcher,
    UnknownCommandTarget,
)

__all__ = ("DuplicateCommandTarget", "InProcessCommandDispatcher", "UnknownCommandTarget")
