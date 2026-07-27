"""One-attempt Skill Runtime implementation for the executable reference flow."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from aieos.ai_gateway import AIGateway
from aieos.capability_registry import CapabilityRegistry
from aieos.contracts import (
    DataClassification,
    ErrorCategory,
    ErrorEnvelope,
    ErrorSeverity,
    LogSeverity,
    ObservabilityContext,
    RedactionStatus,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.contracts.commands import CommandEnvelope
from aieos.contracts.events import EventEnvelope, EventMetadata
from aieos.domain import Clock, IdentifierFactory
from aieos.event_bus import EventOutbox
from aieos.memory_service import MemoryService
from aieos.observability import ObservationRecorder
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import AuthorizationFailure, ScopeAuthorizer
from aieos.skill_registry import SkillRegistry
from aieos.skill_runtime.ports import Skill, SkillInput, SkillServices


class ExecutionState(StrEnum):
    REQUESTED = "Requested"
    EXECUTING = "Executing"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"


@dataclass(slots=True)
class ExecutionRecord:
    execution_id: str
    workflow_id: str
    workflow_step_id: str
    attempt_number: int
    tenant_id: str
    workspace_id: str
    state: ExecutionState
    acknowledgement: ResultEnvelope
    start_event: EventEnvelope | None = None
    terminal_event: EventEnvelope | None = None
    result: ResultEnvelope | None = None
    error: ErrorEnvelope | None = None


@dataclass(slots=True)
class ExecutionCommandReceipt:
    command: CommandEnvelope
    acknowledgement: ResultEnvelope
    completed: bool = False


class InMemoryExecutionRepository:
    """Authoritative attempt state and target-owned Command idempotency receipts."""

    def __init__(self) -> None:
        self.records: dict[str, ExecutionRecord] = {}
        self.command_receipts: dict[str, ExecutionCommandReceipt] = {}

    def add(self, record: ExecutionRecord) -> None:
        if record.execution_id in self.records:
            raise ValueError("ExecutionId already exists")
        self.records[record.execution_id] = record

    def receipt_for_command(self, command_id: str) -> ExecutionCommandReceipt | None:
        return self.command_receipts.get(command_id)

    def begin_command(
        self, command: CommandEnvelope, acknowledgement: ResultEnvelope
    ) -> ExecutionCommandReceipt:
        receipt = ExecutionCommandReceipt(command, acknowledgement)
        self.command_receipts[command.command_id] = receipt
        return receipt

    def complete_command(self, command_id: str) -> ResultEnvelope:
        receipt = self.command_receipts[command_id]
        receipt.completed = True
        return receipt.acknowledgement

    def remember_completed_command(self, command: CommandEnvelope, result: ResultEnvelope) -> None:
        receipt = self.begin_command(command, result)
        receipt.completed = True


class SkillDependencyFailure(RuntimeError):
    """Normalized failure raised by approved Skill code."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.DEPENDENCY_FAILURE,
        retry: RetryClassification = RetryClassification.REQUIRES_POLICY_EVALUATION,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retry = retry


class SkillRuntime:
    """Validate and execute exactly one Workflow Engine-instructed attempt."""

    component_name = "Skill Runtime"

    def __init__(
        self,
        *,
        repository: InMemoryExecutionRepository,
        skills: SkillRegistry,
        skill_implementations: Mapping[str, Skill],
        capabilities: CapabilityRegistry,
        ai_gateway: AIGateway,
        memory_service: MemoryService,
        outbox: EventOutbox,
        authorizer: ScopeAuthorizer,
        outcomes: OutcomeFactory,
        clock: Clock,
        identifiers: IdentifierFactory,
        observations: ObservationRecorder,
        default_timeout_seconds: float = 1.0,
    ) -> None:
        self._repository = repository
        self._skills = skills
        self._skill_implementations = dict(skill_implementations)
        self._capabilities = capabilities
        self._services = SkillServices(ai_gateway, memory_service)
        self._outbox = outbox
        self._authorizer = authorizer
        self._outcomes = outcomes
        self._clock = clock
        self._identifiers = identifiers
        self._observations = observations
        self._default_timeout_seconds = default_timeout_seconds

    async def handle(self, command: CommandEnvelope) -> ResultEnvelope:
        receipt = self._repository.receipt_for_command(command.command_id)
        if receipt is not None:
            if receipt.command != command:
                raise ValueError("CommandId cannot be reused with changed immutable content")
            if receipt.completed:
                return receipt.acknowledgement
        if command.target_component != self.component_name:
            raise ValueError("Command target does not match Skill Runtime")
        if command.command_type != "DispatchExecutionAttempt" or command.execution_id is None:
            return self._reject(command, "SKILL_COMMAND_INVALID", "invalid execution Command")
        try:
            self._authorizer.require(
                command.metadata.authorization,
                permission="skill.execute",
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
            )
        except AuthorizationFailure:
            return self._reject(
                command,
                "SKILL_EXECUTION_UNAUTHORIZED",
                "execution is unauthorized",
            )
        if (
            command.workflow_id is None
            or command.workflow_step_id is None
            or command.metadata.attempt_number is None
        ):
            return self._reject(command, "SKILL_CONTEXT_INVALID", "execution context is incomplete")

        skill_version_id = self._payload_string(command.payload, "skill_version_id")
        definition = self._skills.resolve(skill_version_id)
        capability = self._capabilities.resolve(
            definition.capability_id, definition.capability_contract_version_id
        )
        if capability.implementation_reference != definition.implementation_reference:
            return self._reject(
                command,
                "CAPABILITY_IMPLEMENTATION_MISMATCH",
                "Skill and Capability resolution evidence disagree",
            )
        try:
            implementation = self._skill_implementations[definition.implementation_reference]
        except KeyError:
            return self._reject(
                command, "SKILL_IMPLEMENTATION_MISSING", "approved Skill implementation unavailable"
            )

        if receipt is None:
            acknowledgement = self._outcomes.accepted(
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                value_reference=command.execution_id,
            )
            receipt = self._repository.begin_command(command, acknowledgement)
            record = ExecutionRecord(
                execution_id=command.execution_id,
                workflow_id=command.workflow_id,
                workflow_step_id=command.workflow_step_id,
                attempt_number=command.metadata.attempt_number,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                state=ExecutionState.EXECUTING,
                acknowledgement=acknowledgement,
            )
            record.start_event = self._event_envelope(
                command, "ExecutionAttemptStarted", acknowledgement
            )
            self._repository.add(record)
        else:
            record = self._repository.records[command.execution_id]
            acknowledgement = receipt.acknowledgement

        assert record.start_event is not None
        await self._publish(record.start_event)
        if record.result is not None:
            assert record.terminal_event is not None
            await self._publish(record.terminal_event)
            return self._repository.complete_command(command.command_id)

        timeout = self._payload_float(
            command.payload, "timeout_seconds", self._default_timeout_seconds
        )
        skill_input = SkillInput(
            execution_id=command.execution_id,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            authorization=command.metadata.authorization,
            payload=command.payload,
        )
        try:
            output = await asyncio.wait_for(
                implementation.execute(skill_input, self._services), timeout=timeout
            )
        except TimeoutError:
            terminal, error = self._outcomes.unsuccessful(
                status=ResultStatus.TIMED_OUT,
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                error_code="EXECUTION_ATTEMPT_TIMED_OUT",
                category=ErrorCategory.TIMEOUT,
                severity=ErrorSeverity.WARNING,
                retry=RetryClassification.REQUIRES_POLICY_EVALUATION,
                message="The execution attempt exceeded its allowed duration.",
                predecessor_result_id=acknowledgement.result_id,
            )
            record.state = ExecutionState.TIMED_OUT
            event_type = "ExecutionAttemptTimedOut"
        except SkillDependencyFailure as failure:
            terminal, error = self._outcomes.unsuccessful(
                status=ResultStatus.FAILED,
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                error_code="SKILL_DEPENDENCY_FAILURE",
                category=failure.category,
                severity=ErrorSeverity.WARNING,
                retry=failure.retry,
                message=str(failure),
                predecessor_result_id=acknowledgement.result_id,
            )
            record.state = ExecutionState.FAILED
            event_type = "ExecutionAttemptFailed"
        except Exception:
            terminal, error = self._outcomes.unsuccessful(
                status=ResultStatus.FAILED,
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                error_code="EXECUTION_ATTEMPT_FAILED",
                category=ErrorCategory.EXECUTION_FAILURE,
                severity=ErrorSeverity.ERROR,
                retry=RetryClassification.REQUIRES_POLICY_EVALUATION,
                message="The execution attempt failed.",
                predecessor_result_id=acknowledgement.result_id,
            )
            record.state = ExecutionState.FAILED
            event_type = "ExecutionAttemptFailed"
        else:
            terminal = self._outcomes.succeeded(
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                value_reference=output.value,
                metadata={
                    "memory_id": output.memory_id,
                    "ai_invocation_id": output.ai_invocation_id,
                },
                predecessor_result_id=acknowledgement.result_id,
            )
            error = None
            record.state = ExecutionState.SUCCEEDED
            event_type = "ExecutionAttemptSucceeded"
        record.result = terminal
        record.error = error
        record.terminal_event = self._event_envelope(command, event_type, terminal, error)
        await self._publish(record.terminal_event)
        self._observe(command, terminal)
        return self._repository.complete_command(command.command_id)

    def _reject(self, command: CommandEnvelope, code: str, message: str) -> ResultEnvelope:
        result, _ = self._outcomes.unsuccessful(
            status=ResultStatus.REJECTED,
            subject=command.execution_id or command.command_id,
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
        self._repository.remember_completed_command(command, result)
        return result

    def _event_envelope(
        self,
        command: CommandEnvelope,
        event_type: str,
        result: ResultEnvelope,
        error: ErrorEnvelope | None = None,
    ) -> EventEnvelope:
        now = self._clock.now()
        return EventEnvelope(
            event_id=self._identifiers.new("event"),
            event_type=event_type,
            event_version="1.0",
            occurred_at=now,
            recorded_at=now,
            producer=self.component_name,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            request_id=command.metadata.request_id,
            workflow_id=command.workflow_id,
            workflow_step_id=command.workflow_step_id,
            execution_id=command.execution_id,
            subject=command.execution_id or command.command_id,
            payload={
                "result_id": result.result_id,
                "result_status": result.result_status.value,
                "error_id": error.error_id if error else None,
                "retry_classification": (
                    error.retry_classification.value
                    if error
                    else RetryClassification.NEVER_RETRY.value
                ),
                "attempt_number": command.metadata.attempt_number,
                "value_reference": result.value_reference,
            },
            metadata=EventMetadata(
                trace_id=command.metadata.trace_id, span_id=command.metadata.span_id
            ),
        )

    async def _publish(self, event: EventEnvelope) -> None:
        self._outbox.record(event)
        await self._outbox.drain()

    def _observe(self, command: CommandEnvelope, result: ResultEnvelope) -> None:
        context = ObservabilityContext(
            component_identity=self.component_name,
            operation_name="execute_attempt",
            contract_version="1.0",
            observed_at=self._clock.now(),
            environment_identity="local",
            deployment_identity="reference",
            data_classification=DataClassification.NON_SENSITIVE,
            redaction_status=RedactionStatus.NOT_REQUIRED,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            request_id=command.metadata.request_id,
            command_id=command.command_id,
            workflow_id=command.workflow_id,
            workflow_step_id=command.workflow_step_id,
            execution_id=command.execution_id,
            result_id=result.result_id,
            error_id=result.error_id,
        )
        self._observations.record_log(
            context=context,
            severity=(
                LogSeverity.INFO
                if result.result_status is ResultStatus.SUCCEEDED
                else LogSeverity.ERROR
            ),
            message=f"Execution attempt reached {result.result_status.value}.",
        )

    @staticmethod
    def _payload_string(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _payload_float(payload: Mapping[str, object], key: str, default: float) -> float:
        value = payload.get(key, default)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"{key} must be numeric")
        return float(value)


__all__ = (
    "ExecutionCommandReceipt",
    "ExecutionRecord",
    "InMemoryExecutionRepository",
    "SkillDependencyFailure",
    "SkillRuntime",
)
