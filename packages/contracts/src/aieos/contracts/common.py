"""Shared immutable values used by frozen contract envelopes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Verified caller context propagated to, but revalidated by, the target."""

    actor_id: str
    permissions: frozenset[str]
    tenant_id: str
    workspace_id: str
    policy_id: str
    policy_version_id: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.actor_id,
                self.tenant_id,
                self.workspace_id,
                self.policy_id,
                self.policy_version_id,
            )
        ):
            raise ValueError("authorization context fields must be non-empty")


__all__ = ("AuthorizationContext",)
