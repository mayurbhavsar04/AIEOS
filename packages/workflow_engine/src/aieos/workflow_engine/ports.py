"""Workflow Engine public bootstrap port."""

from typing import Protocol


class RetryDecisionOwner(Protocol):
    """Port establishing Workflow Engine retry-decision ownership."""

    def permits_new_attempt(self, workflow_step_id: str) -> bool: ...


__all__ = ("RetryDecisionOwner",)
