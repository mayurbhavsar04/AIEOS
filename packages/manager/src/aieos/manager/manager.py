"""Manager-owned Request acceptance and Workflow handoff."""

from __future__ import annotations

from collections.abc import Mapping

from aieos.contracts import (
    ErrorCategory,
    ErrorSeverity,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.domain import Clock, IdentifierFactory
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import AuthorizationFailure, ScopeAuthorizer
from aieos.workflow_engine import WorkflowClient


class InMemoryRequestRepository:
    """Manager-owned Request idempotency and terminal response references."""

    def __init__(self) -> None:
        self.command_results: dict[str, ResultEnvelope] = {}
        self.commands: dict[str, CommandEnvelope] = {}
        self.workflow_commands: dict[str, CommandEnvelope] = {}


class Manager:
    """Interpret and accept one Request before initiating an approved Workflow."""

    component_name = "Manager"

    def __init__(
        self,
        *,
        repository: InMemoryRequestRepository,
        workflow_client: WorkflowClient,
        authorizer: ScopeAuthorizer,
        outcomes: OutcomeFactory,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        self._repository = repository
        self._workflow_client = workflow_client
        self._authorizer = authorizer
        self._outcomes = outcomes
        self._clock = clock
        self._identifiers = identifiers

    async def handle(self, command: CommandEnvelope) -> ResultEnvelope:
        cached = self._repository.command_results.get(command.command_id)
        if cached is not None:
            if self._repository.commands[command.command_id] != command:
                raise ValueError("CommandId cannot be reused with changed immutable content")
            return cached
        existing = self._repository.commands.get(command.command_id)
        if existing is not None and existing != command:
            raise ValueError("CommandId cannot be reused with changed immutable content")
        if (
            command.target_component != self.component_name
            or command.command_type != "AcceptRequest"
        ):
            return self._reject(command, "REQUEST_COMMAND_INVALID", "unsupported Manager Command")
        try:
            self._authorizer.require(
                command.metadata.authorization,
                permission="request.accept",
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
            )
        except AuthorizationFailure:
            return self._reject(command, "REQUEST_UNAUTHORIZED", "Request acceptance denied")
        try:
            message = self._payload_string(command.payload, "message")
        except ValueError:
            return self._reject(
                command,
                "REQUEST_PAYLOAD_INVALID",
                "Request message must be a non-empty string",
            )
        start = self._repository.workflow_commands.get(command.command_id)
        if start is None:
            start = CommandEnvelope(
                command_id=self._identifiers.new("command"),
                command_type="StartWorkflow",
                command_version="1.0",
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                target_component="Workflow Engine",
                initiator=self.component_name,
                timestamp=self._clock.now(),
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                payload={
                    "message": message,
                    "workflow_definition_id": "hello-aieos",
                    "workflow_definition_version_id": "hello-aieos-v1",
                    "skill_version_id": "hello-aieos-skill-v1",
                    "max_attempts": command.payload.get("max_attempts", 2),
                    "timeout_seconds": command.payload.get("timeout_seconds", 1.0),
                },
                metadata=CommandMetadata(
                    request_id=command.metadata.request_id,
                    idempotency_key=f"{command.metadata.idempotency_key}:workflow",
                    authorization=command.metadata.authorization,
                ),
            )
            self._repository.commands[command.command_id] = command
            self._repository.workflow_commands[command.command_id] = start
        acknowledgement = await self._workflow_client.submit(start)
        self._repository.commands[command.command_id] = command
        self._repository.command_results[command.command_id] = acknowledgement
        if acknowledgement.value_reference is None:
            return acknowledgement
        outcome = self._workflow_client.outcome(acknowledgement.value_reference)
        result = outcome or acknowledgement
        self._repository.command_results[command.command_id] = result
        return result

    def _reject(self, command: CommandEnvelope, code: str, message: str) -> ResultEnvelope:
        result, _ = self._outcomes.unsuccessful(
            status=ResultStatus.REJECTED,
            subject=command.metadata.request_id,
            producer=self.component_name,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            command_id=command.command_id,
            error_code=code,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.WARNING,
            retry=RetryClassification.NEVER_RETRY,
            message=message,
        )
        self._repository.commands[command.command_id] = command
        self._repository.command_results[command.command_id] = result
        return result

    @staticmethod
    def _payload_string(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value


__all__ = ("InMemoryRequestRepository", "Manager")
