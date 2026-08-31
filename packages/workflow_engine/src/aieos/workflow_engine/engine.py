"""In-memory Workflow Engine for the executable reference flow."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from aieos.command_dispatcher import CommandDispatcher
from aieos.contracts import (
    AuthorizationContext,
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
from aieos.workflow_engine.governance import (
    WorkflowAIBudgetEnvelope,
    admission_binding,
    scale6,
)


class WorkflowState(StrEnum):
    CREATED = "Created"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class CommandProcessingState(StrEnum):
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"


class WorkflowAIAdmissionState(StrEnum):
    """Approved durable lifecycle for one logical Workflow AI admission."""

    REQUESTED = "Requested"
    PENDING_ADMISSION = "PendingAdmission"
    COMMITTED = "Committed"
    GATEWAY_ACCEPTED = "GatewayAccepted"
    SETTLING = "Settling"
    RECONCILED = "Reconciled"
    RELEASED = "Released"
    REJECTED = "Rejected"


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_definition_id: str
    workflow_definition_version_id: str
    skill_version_id: str
    max_attempts: int = 2
    workflow_kind: str = ""
    ai_budget_envelope: Mapping[str, object] | None = None

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
    error: ErrorEnvelope | None = None
    ai_budget_envelope: WorkflowAIBudgetEnvelope | None = None
    ai_admissions: dict[str, Mapping[str, object]] | None = None
    ai_admission_states: dict[str, Mapping[str, object]] | None = None
    transition_version: int = 0

    def __post_init__(self) -> None:
        if self.workflow_events is None:
            self.workflow_events = {}
        if self.retry_commands is None:
            self.retry_commands = {}
        if self.ai_admissions is None:
            self.ai_admissions = {}
        if self.ai_admission_states is None:
            self.ai_admission_states = {}


@dataclass(slots=True)
class WorkflowCommandReceipt:
    command: CommandEnvelope
    result: ResultEnvelope
    workflow_id: str
    error: ErrorEnvelope | None = None
    state: CommandProcessingState = CommandProcessingState.IN_PROGRESS


class InMemoryWorkflowRepository:
    """Authoritative Workflow state and target-owned idempotency receipts."""

    def __init__(self) -> None:
        self._dispatch_context: ContextVar[CommandEnvelope | None] = ContextVar(
            "workflow_dispatch_context", default=None
        )
        self.instances: dict[str, WorkflowInstance] = {}
        self.command_receipts: dict[str, WorkflowCommandReceipt] = {}

    def add(self, instance: WorkflowInstance) -> None:
        if instance.workflow_id in self.instances:
            raise ValueError("WorkflowId already exists")
        self.instances[instance.workflow_id] = instance

    def receipt_for_command(self, command_id: str) -> WorkflowCommandReceipt | None:
        return self.command_receipts.get(command_id)

    def begin_command(
        self,
        command: CommandEnvelope,
        result: ResultEnvelope,
        workflow_id: str,
        error: ErrorEnvelope | None = None,
    ) -> WorkflowCommandReceipt:
        receipt = WorkflowCommandReceipt(command, result, workflow_id, error)
        self.command_receipts[command.command_id] = receipt
        return receipt

    def complete_command(self, command_id: str) -> ResultEnvelope:
        receipt = self.command_receipts[command_id]
        receipt.state = CommandProcessingState.COMPLETED
        return receipt.result

    def remember_completed_command(
        self,
        command: CommandEnvelope,
        result: ResultEnvelope,
        workflow_id: str,
        error: ErrorEnvelope | None = None,
    ) -> None:
        receipt = self.begin_command(command, result, workflow_id, error)
        receipt.state = CommandProcessingState.COMPLETED

    @contextmanager
    def dispatch_context(self, command: CommandEnvelope) -> Iterator[None]:
        """Preserve Engine-owned identity across delivery, including metadata mutation."""
        token = self._dispatch_context.set(command)
        try:
            yield
        finally:
            self._dispatch_context.reset(token)

    async def authoritative_ai_admission(
        self,
        *,
        workflow_id: str,
        command_id: str,
        execution_id: str,
    ) -> Mapping[str, object] | None:
        """Resolve the current Workflow-owned admission without caller evidence."""
        dispatch = self._dispatch_context.get()
        if dispatch is not None and (
            dispatch.workflow_id != workflow_id
            or dispatch.command_id != command_id
            or dispatch.execution_id != execution_id
        ):
            return None
        instance = self.instances.get(workflow_id)
        if instance is None:
            return None
        admissions = instance.ai_admissions or {}
        states = instance.ai_admission_states or {}
        binding = admissions.get(command_id)
        record = states.get(command_id)
        if not isinstance(binding, Mapping) or not isinstance(record, Mapping):
            return None
        version = binding.get("WorkflowAdmissionStateVersion")
        state = record.get("State")
        current_running = instance.state is WorkflowState.RUNNING and state in {
            WorkflowAIAdmissionState.COMMITTED.value,
            WorkflowAIAdmissionState.GATEWAY_ACCEPTED.value,
            WorkflowAIAdmissionState.SETTLING.value,
        }
        current_replay = (
            instance.state is WorkflowState.COMPLETED
            and state == WorkflowAIAdmissionState.RECONCILED.value
        )
        if (
            not (current_running or current_replay)
            or not isinstance(version, int)
            or version != instance.transition_version
            or record.get("WorkflowAdmissionStateVersion") != version
            or record.get("Binding") != binding
            or binding.get("ExecutionId") != execution_id
        ):
            return None
        return binding

    async def owns_ai_dispatch(
        self,
        *,
        workflow_id: str | None,
        command_id: str,
        execution_id: str,
    ) -> bool:
        """Ownership survives invalid identity and absent or released admission."""
        return self._dispatch_context.get() is not None or workflow_id in self.instances


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
        if command.command_version not in {"1", "1.0", "2", "2.0"}:
            return self._reject(
                command,
                "WORKFLOW_COMMAND_VERSION_UNSUPPORTED",
                "unknown Workflow command version cannot use compatibility fallback",
            )
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
        # A terminal Workflow outcome is immutable.  Late redelivery of a
        # different attempt terminal event is expected after cancellation,
        # failover, or a process crash; it must not replace the authoritative
        # result selected by the first terminal transition.
        if instance.state in {
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }:
            instance.processed_event_ids = instance.processed_event_ids | {event.event_id}
            return
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
                decision_id = self._identifiers.new("decision")
                try:
                    retry_command = self._create_attempt_command(instance, decision_id)
                except ValueError as exc:
                    if str(exc) not in {
                        "WORKFLOW_AI_BUDGET_EXHAUSTED",
                        "WORKFLOW_AI_AUTHORIZATION_REVOKED",
                    }:
                        raise
                    await self._reject_retry_admission(instance, event, str(exc))
                    instance.processed_event_ids = instance.processed_event_ids | {event.event_id}
                    return
                self._record_retry_decision(instance, event, decision_id)
                retry_commands[event.event_id] = retry_command
            with self._repository.dispatch_context(retry_command):
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
        raw_budget_envelope = command.payload.get("workflow_ai_budget_envelope")
        budget_envelope = (
            cast(Mapping[str, object], raw_budget_envelope)
            if isinstance(raw_budget_envelope, Mapping)
            else None
        )
        definition = WorkflowDefinition(
            workflow_definition_id=self._payload_string(command.payload, "workflow_definition_id"),
            workflow_definition_version_id=self._payload_string(
                command.payload, "workflow_definition_version_id"
            ),
            skill_version_id=self._payload_string(command.payload, "skill_version_id"),
            max_attempts=self._payload_int(command.payload, "max_attempts", 2),
            workflow_kind=str(command.payload.get("workflow_kind", "")),
            ai_budget_envelope=budget_envelope,
        )
        envelope: WorkflowAIBudgetEnvelope | None = None
        ai_capable_definition = definition.skill_version_id == "structured-task-kind-skill-v1"
        if ai_capable_definition:
            # This is deliberately before instance creation and dispatch: malformed
            # reference-workflow input never reaches Skill Runtime or Gateway.
            statement = command.payload.get("statement")
            if not isinstance(statement, str) or not 1 <= len(statement.strip()) <= 512:
                return self._reject(
                    command,
                    "CLASSIFY_AND_ROUTE_INPUT_INVALID",
                    "statement must contain 1..512 characters",
                )
            if definition.ai_budget_envelope is None:
                return self._reject(
                    command,
                    "WORKFLOW_AI_BUDGET_ENVELOPE_REQUIRED",
                    "AI-capable workflow requires an envelope",
                )
            try:
                envelope = WorkflowAIBudgetEnvelope.parse(definition.ai_budget_envelope)
            except ValueError:
                return self._reject(
                    command, "WORKFLOW_AI_BUDGET_ENVELOPE_INVALID", "unsupported AI budget envelope"
                )
            if (
                envelope.definition_version_id != definition.workflow_definition_version_id
                or envelope.tenant_id != command.tenant_id
                or envelope.workspace_id != command.workspace_id
                or envelope.policy_id != command.metadata.authorization.policy_id
                or envelope.policy_version_id != command.metadata.authorization.policy_version_id
            ):
                return self._reject(
                    command,
                    "WORKFLOW_AI_BUDGET_ENVELOPE_SCOPE_MISMATCH",
                    "AI budget envelope binding mismatch",
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
            ai_budget_envelope=envelope,
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
        if instance.outcome is not None:
            return self._repository.complete_command(receipt.command.command_id)
        await self._publish_workflow_event(instance, "WorkflowStarted", receipt.command.command_id)
        if instance.initial_attempt_command is None:
            try:
                instance.initial_attempt_command = self._create_attempt_command(
                    instance, receipt.command.command_id
                )
            except ValueError as exc:
                if str(exc) not in {
                    "WORKFLOW_AI_BUDGET_EXHAUSTED",
                    "WORKFLOW_AI_AUTHORIZATION_REVOKED",
                }:
                    raise
                code = str(exc)
                result, error = self._outcomes.unsuccessful(
                    status=ResultStatus.REJECTED,
                    subject=instance.workflow_id,
                    producer=self.component_name,
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                    correlation_id=instance.correlation_id,
                    causation_id=receipt.command.command_id,
                    command_id=receipt.command.command_id,
                    error_code=code,
                    category=(
                        ErrorCategory.AUTHORIZATION
                        if code == "WORKFLOW_AI_AUTHORIZATION_REVOKED"
                        else ErrorCategory.VALIDATION
                    ),
                    severity=ErrorSeverity.WARNING,
                    retry=RetryClassification.NEVER_RETRY,
                    message=(
                        "Workflow AI authorization is unavailable before admission."
                        if code == "WORKFLOW_AI_AUTHORIZATION_REVOKED"
                        else "Workflow AI budget is exhausted before Gateway dispatch."
                    ),
                )
                instance.state = WorkflowState.FAILED
                instance.outcome = result
                instance.error = error
                receipt.result = result
                receipt.error = error
                return self._repository.complete_command(receipt.command.command_id)
        with self._repository.dispatch_context(instance.initial_attempt_command):
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
        if instance.state is WorkflowState.CANCELLED:
            # A crash may occur after the authoritative cancellation outcome
            # commits but before this command receipt does.  Rebuild that
            # receipt from the immutable terminal outcome rather than minting
            # a second cancellation result on recovery.
            if instance.outcome is None:
                raise RuntimeError("cancelled Workflow is missing its terminal outcome")
            self._repository.remember_completed_command(
                command,
                instance.outcome,
                instance.workflow_id,
                instance.error,
            )
            return instance.outcome
        if instance.state in {WorkflowState.COMPLETED, WorkflowState.FAILED}:
            return self._reject(
                command, "WORKFLOW_ALREADY_TERMINAL", "terminal Workflow cannot be cancelled"
            )
        instance.state = WorkflowState.CANCELLED
        result, error = self._outcomes.unsuccessful(
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
        instance.error = error
        self._repository.remember_completed_command(
            command,
            result,
            instance.workflow_id,
            error,
        )
        return result

    def _create_attempt_command(
        self, instance: WorkflowInstance, causation_id: str
    ) -> CommandEnvelope:
        envelope = instance.ai_budget_envelope
        governed_ai_route = instance.definition.skill_version_id == "structured-task-kind-skill-v1"
        if governed_ai_route and instance.input_payload.get("authoritative_result_id") is None:
            try:
                self._authorizer.require(
                    instance.authorization,
                    permission="ai.invoke",
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                )
            except AuthorizationFailure as error:
                raise ValueError("WORKFLOW_AI_AUTHORIZATION_REVOKED") from error
            assert envelope is not None
            if (
                instance.authorization.policy_id != envelope.policy_id
                or instance.authorization.policy_version_id != envelope.policy_version_id
            ):
                raise ValueError("WORKFLOW_AI_AUTHORIZATION_REVOKED")
        admission: Mapping[str, object] | None = None
        committed: int | None = None
        if (
            governed_ai_route
            and instance.authorization
            and instance.input_payload.get("authoritative_result_id") is None
        ):
            # Reuse/bypass is deliberately zero-cost and does not fabricate an AI id.
            committed = 10_000  # approved structured package maximum: USD 0.01
            admissions = instance.ai_admissions
            admission_states = instance.ai_admission_states
            assert admissions is not None
            assert admission_states is not None
            used = 0
            for item in admissions.values():
                raw_exposure = item.get("CommittedExposure")
                if not isinstance(raw_exposure, Mapping):
                    raise ValueError("WORKFLOW_AI_ADMISSION_STATE_INVALID")
                exposure = cast(Mapping[str, object], raw_exposure)
                amount = exposure.get("Amount")
                if not isinstance(amount, str):
                    raise ValueError("WORKFLOW_AI_ADMISSION_STATE_INVALID")
                used += scale6(amount)
            assert envelope is not None
            if used + committed > envelope.ceiling_microusd:
                raise ValueError("WORKFLOW_AI_BUDGET_EXHAUSTED")
        instance.attempt_number += 1
        execution_id = self._identifiers.new("execution")
        instance.execution_ids = (*instance.execution_ids, execution_id)
        command_id = self._identifiers.new("command")
        if committed is not None:
            admissions = instance.ai_admissions
            admission_states = instance.ai_admission_states
            assert admissions is not None
            assert admission_states is not None
            assert envelope is not None
            instance.transition_version += 1
            logical_key = ":".join(
                (
                    instance.tenant_id,
                    instance.workspace_id,
                    instance.workflow_id,
                    instance.workflow_step_id,
                    command_id,
                    execution_id,
                )
            )
            admission_states[command_id] = {
                "State": WorkflowAIAdmissionState.REQUESTED.value,
                "LogicalAdmissionKey": logical_key,
                "WorkflowAdmissionStateVersion": instance.transition_version,
                "CommittedExposure": None,
                "GatewayEvidence": None,
                "SettledActual": None,
                "Disposition": "admission_requested",
            }
            admission_states[command_id] = {
                **admission_states[command_id],
                "State": WorkflowAIAdmissionState.PENDING_ADMISSION.value,
                "Disposition": "authority_scope_and_accounting_validated",
            }
            admission = admission_binding(
                envelope=envelope,
                workflow_id=instance.workflow_id,
                workflow_step_id=instance.workflow_step_id,
                command_id=command_id,
                execution_id=execution_id,
                skill_version_id=instance.definition.skill_version_id,
                capability_id="StructuredTaskKindClassification",
                capability_contract_version_id="1",
                state_version=instance.transition_version,
                committed_microusd=committed,
            )
            admissions[command_id] = admission
            admission_states[command_id] = {
                **admission_states[command_id],
                "State": WorkflowAIAdmissionState.COMMITTED.value,
                "CommittedExposure": admission["CommittedExposure"],
                "GatewayIdempotencyKey": admission["GatewayIdempotencyKey"],
                "Binding": admission,
                "Disposition": "committed_before_gateway_handoff",
            }
        return CommandEnvelope(
            command_id=command_id,
            command_type="DispatchExecutionAttempt",
            command_version="2.0" if governed_ai_route else "1.0",
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
            payload=(
                {"statement": str(instance.input_payload["statement"]).strip()}
                if governed_ai_route
                else {
                    **instance.input_payload,
                    "skill_version_id": instance.definition.skill_version_id,
                    "timeout_seconds": instance.timeout_seconds,
                }
            ),
            metadata=CommandMetadata(
                request_id=instance.request_id,
                idempotency_key=f"{instance.workflow_step_id}:{instance.attempt_number}",
                authorization=instance.authorization,
                attempt_number=instance.attempt_number,
                skill_version_id=(
                    instance.definition.skill_version_id if governed_ai_route else None
                ),
                authoritative_result_id=(
                    str(instance.input_payload["authoritative_result_id"])
                    if instance.input_payload.get("authoritative_result_id")
                    else None
                ),
                workflow_ai_budget_admission=admission,
            ),
        )

    async def _complete(self, instance: WorkflowInstance, event: EventEnvelope) -> None:
        raw_lineage = event.payload.get("audit_lineage")
        if not isinstance(raw_lineage, Mapping):
            await self._fail(instance, event)
            return
        lineage = dict(cast(Mapping[str, object], raw_lineage))
        if (
            lineage.get("tenant_id") != instance.tenant_id
            or lineage.get("workspace_id") != instance.workspace_id
            or lineage.get("workflow_id") != instance.workflow_id
            or lineage.get("workflow_step_id") != instance.workflow_step_id
            or lineage.get("execution_id") != event.execution_id
            or event.execution_id not in instance.execution_ids
        ):
            await self._fail(instance, event)
            return
        lineage["capability_result_id"] = event.payload.get("result_id")
        value = event.payload.get("value_reference")
        if instance.definition.workflow_kind == "ClassifyAndRouteTask":
            routes = {
                "Question": "question_queue",
                "Instruction": "instruction_queue",
                "Statement": "information_queue",
            }
            try:
                task_kind = json.loads(str(value))["task_kind"]
                route = routes[task_kind]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                await self._fail(instance, event)
                return
            envelope = instance.ai_budget_envelope
            assert envelope is not None
            value = json.dumps(
                {
                    "task_kind": task_kind,
                    "route": route,
                    "workflow_id": instance.workflow_id,
                    "workflow_step_id": instance.workflow_step_id,
                    "execution_id": event.execution_id,
                    "capability_result_id": event.payload.get("result_id"),
                    "governance_evidence": {
                        "workflow_definition_version_id": envelope.definition_version_id,
                        "policy_id": envelope.policy_id,
                        "policy_version_id": envelope.policy_version_id,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        audit_metadata: dict[str, object] = {"audit_lineage": lineage}
        envelope = instance.ai_budget_envelope
        if envelope is not None:
            avoided = lineage.get("avoided_model_calls")
            settled_microusd, committed_microusd = self._reconcile_ai_admission(
                instance, lineage, bypassed=avoided == 1
            )
            counted_microusd = settled_microusd + committed_microusd
            committed_amount = (
                f"{committed_microusd // 1_000_000}.{committed_microusd % 1_000_000:06d}".rstrip(
                    "0"
                ).rstrip(".")
            )
            settled_amount = (
                f"{settled_microusd // 1_000_000}.{settled_microusd % 1_000_000:06d}".rstrip(
                    "0"
                ).rstrip(".")
            )
            remaining_microusd = envelope.ceiling_microusd - counted_microusd
            remaining_amount = (
                f"{remaining_microusd // 1_000_000}.{remaining_microusd % 1_000_000:06d}".rstrip(
                    "0"
                ).rstrip(".")
            )
            audit_metadata["workflow_ai_budget_evidence"] = {
                "contract_version": 1,
                "source": "accepted_workflow_budget_envelope",
                "workflow_definition_version_id": envelope.definition_version_id,
                "policy_id": envelope.policy_id,
                "policy_version_id": envelope.policy_version_id,
                "admission_decision": "bypassed" if avoided == 1 else "committed",
                "logical_admission_binding": (
                    "not_applicable_by_design"
                    if avoided == 1
                    else lineage.get("command_id", "not_exposed")
                ),
                "conservative_committed_exposure": committed_amount or "0",
                "gateway_authoritative_settled_actual": settled_amount or "0",
                "remaining_workflow_budget": remaining_amount or "0",
                "ai_calls_made": 0 if avoided == 1 else 1,
                "ai_calls_avoided": 1 if avoided == 1 else 0,
                "gateway_accounting": lineage.get("accounting_evidence", "not_exposed"),
                "replay_recovery_decision": "original_or_idempotent_replay",
            }
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
                **audit_metadata,
            },
        )
        instance.state = WorkflowState.COMPLETED
        await self._publish_workflow_event(instance, "WorkflowCompleted", event.event_id)
        self._observe(instance, instance.outcome)

    def _reconcile_ai_admission(
        self,
        instance: WorkflowInstance,
        lineage: Mapping[str, object],
        *,
        bypassed: bool,
    ) -> tuple[int, int]:
        """Project matching Gateway authority; retain every unresolved commitment."""
        states = instance.ai_admission_states or {}
        if bypassed:
            return (0, 0)
        command_id = lineage.get("command_id")
        if not isinstance(command_id, str) or command_id not in states:
            raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISSING")
        record: dict[str, object] = dict(states[command_id])
        binding = record.get("Binding")
        accounting = lineage.get("accounting_evidence")
        if not isinstance(binding, Mapping) or not isinstance(accounting, Mapping):
            raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISSING")
        scoped_accounting = cast(Mapping[str, object], accounting)
        raw_gateway = scoped_accounting.get("gateway_evidence")
        if not isinstance(raw_gateway, Mapping):
            raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISSING")
        gateway = cast(Mapping[str, object], raw_gateway)
        actual = gateway.get("actual_cost")
        invocation_id = lineage.get("ai_invocation_id")
        gateway_result_id = lineage.get("gateway_result_id")
        if (
            gateway.get("evidence_version") != 1
            or gateway.get("status") != "settled"
            or gateway.get("tenant_id") != instance.tenant_id
            or gateway.get("workspace_id") != instance.workspace_id
            or gateway.get("ai_invocation_id") != invocation_id
            or gateway.get("settled_result_id") != gateway_result_id
            or gateway.get("currency_or_reference_unit") != "USD"
            or not isinstance(actual, str)
        ):
            raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISMATCH")
        scale6(actual)
        record.update(
            {
                "State": WorkflowAIAdmissionState.GATEWAY_ACCEPTED.value,
                "AIInvocationId": invocation_id,
                "GatewayEvidence": dict(gateway),
                "Disposition": "gateway_acceptance_correlated",
            }
        )
        record["State"] = WorkflowAIAdmissionState.SETTLING.value
        record["Disposition"] = "gateway_terminal_accounting_validating"
        record["State"] = WorkflowAIAdmissionState.RECONCILED.value
        record["SettledActual"] = {
            "Amount": actual,
            "CurrencyOrReferenceUnit": "USD",
        }
        record["Disposition"] = "gateway_authoritative_actual_settled"
        states[command_id] = record
        settled = 0
        committed = 0
        for candidate in states.values():
            scoped_candidate = candidate
            candidate_state = scoped_candidate.get("State")
            if candidate_state == WorkflowAIAdmissionState.RECONCILED.value:
                settled_actual = scoped_candidate.get("SettledActual")
                if not isinstance(settled_actual, Mapping):
                    raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISSING")
                amount = cast(Mapping[str, object], settled_actual).get("Amount")
                if not isinstance(amount, str):
                    raise ValueError("WORKFLOW_AI_GATEWAY_EVIDENCE_MISSING")
                settled += scale6(amount)
            elif candidate_state in {
                WorkflowAIAdmissionState.COMMITTED.value,
                WorkflowAIAdmissionState.GATEWAY_ACCEPTED.value,
                WorkflowAIAdmissionState.SETTLING.value,
            }:
                exposure = scoped_candidate.get("CommittedExposure")
                if not isinstance(exposure, Mapping):
                    raise ValueError("WORKFLOW_AI_ADMISSION_STATE_INVALID")
                amount = cast(Mapping[str, object], exposure).get("Amount")
                if not isinstance(amount, str):
                    raise ValueError("WORKFLOW_AI_ADMISSION_STATE_INVALID")
                committed += scale6(amount)
        return settled, committed

    async def _fail(self, instance: WorkflowInstance, event: EventEnvelope) -> None:
        instance.state = WorkflowState.FAILED
        instance.outcome, instance.error = self._outcomes.unsuccessful(
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

    async def _reject_retry_admission(
        self, instance: WorkflowInstance, event: EventEnvelope, code: str
    ) -> None:
        instance.state = WorkflowState.FAILED
        instance.outcome, instance.error = self._outcomes.unsuccessful(
            status=ResultStatus.REJECTED,
            subject=instance.workflow_id,
            producer=self.component_name,
            tenant_id=instance.tenant_id,
            workspace_id=instance.workspace_id,
            correlation_id=instance.correlation_id,
            causation_id=event.event_id,
            event_id=event.event_id,
            error_code=code,
            category=(
                ErrorCategory.AUTHORIZATION
                if code == "WORKFLOW_AI_AUTHORIZATION_REVOKED"
                else ErrorCategory.VALIDATION
            ),
            severity=ErrorSeverity.WARNING,
            retry=RetryClassification.NEVER_RETRY,
            message=(
                "Workflow AI authorization is unavailable before retry admission."
                if code == "WORKFLOW_AI_AUTHORIZATION_REVOKED"
                else "Workflow AI budget is exhausted before retry Gateway dispatch."
            ),
        )
        await self._publish_workflow_event(instance, "WorkflowFailed", event.event_id)
        self._observe(instance, instance.outcome)

    def _record_retry_decision(
        self, instance: WorkflowInstance, event: EventEnvelope, decision_id: str
    ) -> None:
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
        result, error = self._outcomes.unsuccessful(
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
            command,
            result,
            command.workflow_id or command.command_id,
            error,
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
