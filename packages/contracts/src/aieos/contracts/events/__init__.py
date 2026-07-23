"""Canonical immutable ES-005 Event envelope."""

from aieos.contracts.events.models import EventEnvelope, EventMetadata

EventMessage = EventEnvelope

__all__ = ("EventEnvelope", "EventMessage", "EventMetadata")
