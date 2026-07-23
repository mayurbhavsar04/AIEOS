"""Workflow Engine public ports."""

from typing import Protocol

from aieos.contracts.commands import CommandEnvelope
from aieos.contracts.results import ResultEnvelope


class RetryDecisionOwner(Protocol):
    """Port establishing Workflow Engine retry-decision ownership."""

    def permits_new_attempt(self, workflow_step_id: str) -> bool: ...


class WorkflowClient(Protocol):
    """Manager-facing Workflow command and outcome boundary."""

    async def submit(self, command: CommandEnvelope) -> ResultEnvelope: ...

    def outcome(self, workflow_id: str) -> ResultEnvelope | None: ...


__all__ = ("RetryDecisionOwner", "WorkflowClient")
