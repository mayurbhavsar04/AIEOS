"""Non-authoritative typing boundary for frozen ES-004 Commands."""

from typing import Protocol


class CommandMessage(Protocol):
    """Opaque Command accepted by bootstrap ports; ES-004 owns its envelope."""

    pass


__all__ = ("CommandMessage",)
