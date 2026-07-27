"""In-memory Workflow Engine for the executable reference flow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from aieos.command_dispatcher import CommandDispatcher
from aieos.contracts import (
    AuthorizationContext,
    DataClassification,
    ErrorCategory,
    ErrorSeverity,
    LogSeverity,
    ObservabilityContext,
    RedactionStatus,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.contracts.events import EventEnvelope, EventMetadata
from aieos.domain import (
    Clock,
    DecisionEvidence,
    IdentifierFactory,
    InMemoryDecisionEvidenceRepository,
)
from aieos.event_bus import EventOutbox
from aieos.observability import ObservationRecorder
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import AuthorizationFailure, ScopeAuthorizer


class WorkflowState(StrEnum):
    CREATED = "Created"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class CommandProcessingState(StrEnum):
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_definition_id: str
    workflow_definition_version_id: str
    skill_version_id: str
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(slots=True)
class WorkflowInstance:
    workflow_id: str
    workflow_step_id: str
    definition: WorkflowDefinition
    tenant_id: str
    workspace_id: str
    request_id: str
    correlation_id: str
    authorization: AuthorizationContext
    input_payload: Mapping[str, object]
    timeout_seconds: float
    state: WorkflowState = WorkflowState.CREATED
    attempt_number: int = 0
    execution_ids: tuple[str, ...] = ()
    processed_event_ids: frozenset[str] = frozenset()
    workflow_events: dict[str, EventEnvelope] | None = None
    initial_attempt_command: CommandEnvelope | None = None
    retry_commands: dict[str, CommandEnvelope] | None = None
    outcome: ResultEnvelope | None = None

    def __post_init__(self) -> None:
        if self.workflow_events is None:
            self.workflow_events = {}
        if self.retry_commands is None:
            self.retry_commands = {}


@dataclass(slots=True)
class WorkflowCommandReceipt:
    command: CommandEnvelope
    result: ResultEnvelope
    workflow_id: str
    state: CommandProcessingState = CommandProcessingState.IN_PROGRESS


class InMemoryWorkflowRepository:
    """Authoritative Workflow state and target-owned idempotency receipts."""

    def __init__(self) -> None:
        self.instances: dict[str, WorkflowInstance] = {}
        self.command_receipts: dict[str, WorkflowCommandReceipt] = {}

    def add(self, instance: WorkflowInstance) -> None:
        if instance.workflow_id in self.instances:
            raise ValueError("WorkflowId already exists")
        self.instances[instance.workflow_id] = instance

    def receipt_for_command(self, command_id: str) -> WorkflowCommandReceipt | None:
        return self.command_receipts.get(command_id)

    def begin_command(
        self, command: CommandEnvelope, result: ResultEnvelope, workflow_id: str
    ) -> WorkflowCommandReceipt:
        receipt = WorkflowCommandReceipt(command, result, workflow_id)
        self.command_receipts[command.command_id] = receipt
        return receipt

    def complete_command(self, command_id: str) -> ResultEnvelope:
        receipt = self.command_receipts[command_id]
        receipt.state = CommandProcessingState.COMPLETED
        return receipt.result

    def remember_completed_command(
        self, command: CommandEnvelope, result: ResultEnvelope, workflow_id: str
    ) -> None:
        receipt = self.begin_command(command, result, workflow_id)
        receipt.state = CommandProcessingState.COMPLETED


class WorkflowEngine:
    """Own Workflow state, transitions, retry decisions, and new ExecutionId values."""

    component_name = "Workflow Engine"

    def __init__(
        self,
        *,
        repository: InMemoryWorkflowRepository,
        dispatcher: CommandDispatcher,
        outbox: EventOutbox,
        authorizer: ScopeAuthorizer,
        outcomes: OutcomeFactory,
        clock: Clock,
        identifiers: IdentifierFactory,
        observations: ObservationRecorder,
        decisions: InMemoryDecisionEvidenceRepository,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher
        self._outbox = outbox
        self._authorizer = authorizer
        self._outcomes = outcomes
        self._clock = clock
        self._identifiers = identifiers
        self._observations = observations
        self._decisions = decisions

    async def handle(self, command: CommandEnvelope) -> ResultEnvelope:
        receipt = self._repository.receipt_for_command(command.command_id)
        if receipt is not None:
            if receipt.command != command:
                raise ValueError("CommandId cannot be reused with changed immutable content")
            if receipt.state is CommandProcessingState.COMPLETED:
                return receipt.result
            if command.command_type == "StartWorkflow":
                return await self._resume_start(receipt)
        if command.target_component != self.component_name:
            raise ValueError("Command target does not match Workflow Engine")
        if command.command_type == "StartWorkflow":
            return await self._start(command)
        if command.command_type == "CancelWorkflow":
            return await self._cancel(command)
        return self._reject(command, "WORKFLOW_COMMAND_INVALID", "unsupported Workflow Command")

    def outcome(self, workflow_id: str) -> ResultEnvelope | None:
        try:
            return self._repository.instances[workflow_id].outcome
        except KeyError:
            return None

    def permits_new_attempt(self, workflow_step_id: str) -> bool:
        instance = next(
            (
                candidate
                for candidate in self._repository.instances.values()
                if candidate.workflow_step_id == workflow_step_id
            ),
            None,
        )
        return (
            instance is not None
            and instance.state is WorkflowState.RUNNING
            and instance.attempt_number < instance.definition.max_attempts
        )

    async def consume(self, event: EventEnvelope) -> None:
        if event.event_type not in {
            "ExecutionAttemptSucceeded",
            "ExecutionAttemptFailed",
            "ExecutionAttemptTimedOut",
        }:
            return
        if event.workflow_id is None:
            raise ValueError("attempt Event must reference WorkflowId")
        instance = self._repository.instances[event.workflow_id]
        if event.event_id in instance.processed_event_ids:
            return
        if event.tenant_id != instance.tenant_id or event.workspace_id != instance.workspace_id:
            raise PermissionError("cross-scope Event delivery denied")
        if event.event_type == "ExecutionAttemptSucceeded":
            await self._complete(instance, event)
            instance.processed_event_ids = instance.processed_event_ids | {event.event_id}
            return
        retry = str(event.payload.get("retry_classification", "NeverRetry"))
        retry_commands = instance.retry_commands
        assert retry_commands is not None
        retry_command = retry_commands.get(event.event_id)
        if retry_command is not None or self._retry_allowed(instance, retry):
            if retry_command is None:
                decision_id = self._record_retry_decision(instance, event)
                retry_command = self._create_attempt_command(instance, decision_id)
                retry_commands[event.event_id] = retry_command
            await self._dispatcher.dispatch(retry_command)
            instance.processed_event_ids = instance.processed_event_ids | {event.event_id}
            return
        await self._fail(instance, event)
        instance.processed_event_ids = instance.processed_event_ids | {event.event_id}

    async def _start(self, command: CommandEnvelope) -> ResultEnvelope:
        try:
            self._authorizer.require(
                command.metadata.authorization,
                permission="workflow.start",
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
            )
        except AuthorizationFailure:
            return self._reject(command, "WORKFLOW_START_UNAUTHORIZED", "Workflow start denied")
        definition = WorkflowDefinition(
            workflow_definition_id=self._payload_string(command.payload, "workflow_definition_id"),
            workflow_definition_version_id=self._payload_string(
                command.payload, "workflow_definition_version_id"
            ),
            skill_version_id=self._payload_string(command.payload, "skill_version_id"),
            max_attempts=self._payload_int(command.payload, "max_attempts", 2),
        )
        workflow_id = self._identifiers.new("workflow")
        instance = WorkflowInstance(
            workflow_id=workflow_id,
            workflow_step_id=self._identifiers.new("step"),
            definition=definition,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            request_id=command.metadata.request_id,
            correlation_id=command.correlation_id,
            authorization=command.metadata.authorization,
            input_payload=command.payload,
            timeout_seconds=self._payload_float(command.payload, "timeout_seconds", 1.0),
            state=WorkflowState.RUNNING,
        )
        self._repository.add(instance)
        acknowledgement = self._outcomes.accepted(
            subject=workflow_id,
            producer=self.component_name,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            command_id=command.command_id,
            value_reference=workflow_id,
        )
        receipt = self._repository.begin_command(command, acknowledgement, workflow_id)
        return await self._resume_start(receipt)

    async def _resume_start(self, receipt: WorkflowCommandReceipt) -> ResultEnvelope:
        instance = self._repository.instances[receipt.workflow_id]
        await self._publish_workflow_event(instance, "WorkflowStarted", receipt.command.command_id)
        if instance.initial_attempt_command is None:
            instance.initial_attempt_command = self._create_attempt_command(
                instance, receipt.command.command_id
            )
        await self._dispatcher.dispatch(instance.initial_attempt_command)
        return self._repository.complete_command(receipt.command.command_id)

    async def _cancel(self, command: CommandEnvelope) -> ResultEnvelope:
        if command.workflow_id is None:
            return self._reject(command, "WORKFLOW_ID_REQUIRED", "WorkflowId is required")
        try:
            self._authorizer.require(
                command.metadata.authorization,
                permission="workflow.cancel",
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
            )
        except AuthorizationFailure:
            return self._reject(
                command,
                "WORKFLOW_CANCEL_UNAUTHORIZED",
                "Workflow cancellation denied",
            )
        try:
            instance = self._repository.instances[command.workflow_id]
        except KeyError:
            return self._reject(command, "WORKFLOW_NOT_FOUND", "Workflow does not exist")
        if instance.tenant_id != command.tenant_id or instance.workspace_id != command.workspace_id:
            return self._reject(
                command,
                "WORKFLOW_SCOPE_MISMATCH",
                "Workflow does not belong to the command scope",
            )
        if instance.state in {WorkflowState.COMPLETED, WorkflowState.FAILED}:
            return self._reject(
                command, "WORKFLOW_ALREADY_TERMINAL", "terminal Workflow cannot be cancelled"
            )
        instance.state = WorkflowState.CANCELLED
        result, _ = self._outcomes.unsuccessful(
            status=ResultStatus.CANCELLED,
            subject=instance.workflow_id,
            producer=self.component_name,
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            correlation_id=instance.correlation_id,
            causation_id=command.command_id,
            command_id=command.command_id,
            error_code="WORKFLOW_CANCELLED",
            category=ErrorCategory.CANCELLATION,
            severity=ErrorSeverity.INFORMATIONAL,
            retry=RetryClassification.NEVER_RETRY,
            message="Workflow cancellation became authoritative.",
        )
        instance.outcome = result
        self._repository.remember_completed_command(command, result, instance.workflow_id)
        return result

    def _create_attempt_command(
        self, instance: WorkflowInstance, causation_id: str
    ) -> CommandEnvelope:
        instance.attempt_number += 1
        execution_id = self._identifiers.new("execution")
        instance.execution_ids = (*instance.execution_ids, execution_id)
        return CommandEnvelope(
            command_id=self._identifiers.new("command"),
            command_type="DispatchExecutionAttempt",
            command_version="1.0",
            correlation_id=instance.correlation_id,
            causation_id=causation_id,
            workflow_id=instance.workflow_id,
            workflow_step_id=instance.workflow_step_id,
            execution_id=execution_id,
            target_component="Skill Runtime",
            initiator=self.component_name,
            timestamp=self._clock.now(),
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            payload={
                **instance.input_payload,
                "skill_version_id": instance.definition.skill_version_id,
                "timeout_seconds": instance.timeout_seconds,
            },
            metadata=CommandMetadata(
                request_id=instance.request_id,
                idempotency_key=f"{instance.workflow_step_id}:{instance.attempt_number}",
                authorization=instance.authorization,
                attempt_number=instance.attempt_number,
            ),
        )

    async def _complete(self, instance: WorkflowInstance, event: EventEnvelope) -> None:
        instance.state = WorkflowState.COMPLETED
        value = event.payload.get("value_reference")
        instance.outcome = self._outcomes.succeeded(
            subject=instance.workflow_id,
            producer=self.component_name,
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            correlation_id=instance.correlation_id,
            causation_id=event.event_id,
            event_id=event.event_id,
            value_reference=value if isinstance(value, str) else None,
            metadata={
                "attempt_count": instance.attempt_number,
                "execution_ids": instance.execution_ids,
            },
        )
        await self._publish_workflow_event(instance, "WorkflowCompleted", event.event_id)
        self._observe(instance, instance.outcome)

    async def _fail(self, instance: WorkflowInstance, event: EventEnvelope) -> None:
        instance.state = WorkflowState.FAILED
        instance.outcome, _ = self._outcomes.unsuccessful(
            status=ResultStatus.FAILED,
            subject=instance.workflow_id,
            producer=self.component_name,
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            correlation_id=instance.correlation_id,
            causation_id=event.event_id,
            event_id=event.event_id,
            error_code="WORKFLOW_ATTEMPTS_EXHAUSTED",
            category=ErrorCategory.WORKFLOW_STATE,
            severity=ErrorSeverity.ERROR,
            retry=RetryClassification.NEVER_RETRY,
            message="Workflow failed after permitted attempts were exhausted.",
        )
        await self._publish_workflow_event(instance, "WorkflowFailed", event.event_id)
        self._observe(instance, instance.outcome)

    def _record_retry_decision(self, instance: WorkflowInstance, event: EventEnvelope) -> str:
        decision_id = self._identifiers.new("decision")
        self._decisions.record(
            DecisionEvidence(
                decision_id=decision_id,
                decision_type="RetryExecutionAttempt",
                component=self.component_name,
                tenant_id=instance.tenant_id,
                workspace_id=instance.workspace_id,
                correlation_id=instance.correlation_id,
                recorded_at=self._clock.now(),
                triggering_id=event.event_id,
            )
        )
        return decision_id

    def _retry_allowed(self, instance: WorkflowInstance, classification: str) -> bool:
        return self.permits_new_attempt(instance.workflow_step_id) and classification in {
            RetryClassification.RETRYABLE.value,
            RetryClassification.RETRYABLE_AFTER_DELAY.value,
            RetryClassification.REQUIRES_POLICY_EVALUATION.value,
        }

    async def _publish_workflow_event(
        self, instance: WorkflowInstance, event_type: str, causation_id: str
    ) -> None:
        now = self._clock.now()
        events = instance.workflow_events
        assert events is not None
        event = events.get(event_type)
        if event is None:
            event = EventEnvelope(
                event_id=self._identifiers.new("event"),
                event_type=event_type,
                event_version="1.0",
                occurred_at=now,
                recorded_at=now,
                producer=self.component_name,
                tenant_id=instance.tenant_id,
                workspace_id=instance.workspace_id,
                correlation_id=instance.correlation_id,
                causation_id=causation_id,
                request_id=instance.request_id,
                workflow_id=instance.workflow_id,
                workflow_step_id=instance.workflow_step_id,
                subject=instance.workflow_id,
                payload={
                    "state": instance.state.value,
                    "result_id": instance.outcome.result_id if instance.outcome else None,
                },
                metadata=EventMetadata(),
            )
            events[event_type] = event
        self._outbox.record(event)
        await self._outbox.drain()

    def _reject(self, command: CommandEnvelope, code: str, message: str) -> ResultEnvelope:
        result, _ = self._outcomes.unsuccessful(
            status=ResultStatus.REJECTED,
            subject=command.workflow_id or command.command_id,
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
        self._repository.remember_completed_command(
            command, result, command.workflow_id or command.command_id
        )
        return result

    def _observe(self, instance: WorkflowInstance, result: ResultEnvelope) -> None:
        context = ObservabilityContext(
            component_identity=self.component_name,
            operation_name="workflow_terminal",
            contract_version="1.0",
            observed_at=self._clock.now(),
            environment_identity="local",
            deployment_identity="reference",
            data_classification=DataClassification.NON_SENSITIVE,
            redaction_status=RedactionStatus.NOT_REQUIRED,
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            correlation_id=instance.correlation_id,
            causation_id=result.causation_id,
            request_id=instance.request_id,
            workflow_id=instance.workflow_id,
            workflow_step_id=instance.workflow_step_id,
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
            message=f"Workflow reached {result.result_status.value}.",
            attributes={"attempt_count": instance.attempt_number},
        )

    @staticmethod
    def _payload_string(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _payload_int(payload: Mapping[str, object], key: str, default: int) -> int:
        value = payload.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        return value

    @staticmethod
    def _payload_float(payload: Mapping[str, object], key: str, default: float) -> float:
        value = payload.get(key, default)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"{key} must be numeric")
        return float(value)


__all__ = (
    "CommandProcessingState",
    "InMemoryWorkflowRepository",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowInstance",
    "WorkflowState",
)
