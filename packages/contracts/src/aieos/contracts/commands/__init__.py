"""Canonical immutable ES-004 Command envelope."""

from aieos.contracts.commands.models import CommandEnvelope, CommandMetadata

CommandMessage = CommandEnvelope

__all__ = ("CommandEnvelope", "CommandMessage", "CommandMetadata")
