"""Non-authoritative typing boundary for frozen ES-005 Events."""

from typing import Protocol


class EventMessage(Protocol):
    """Opaque Event accepted by bootstrap ports; ES-005 owns its envelope."""

    pass


__all__ = ("EventMessage",)
