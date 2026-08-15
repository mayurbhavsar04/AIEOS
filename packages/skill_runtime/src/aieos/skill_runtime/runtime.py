"""One-attempt Skill Runtime implementation for the executable reference flow."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

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
from aieos.skill_runtime.ports import Skill, SkillInput, SkillOutput, SkillServices


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
    error: ErrorEnvelope | None = None
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
        self,
        command: CommandEnvelope,
        acknowledgement: ResultEnvelope,
        error: ErrorEnvelope | None = None,
    ) -> ExecutionCommandReceipt:
        receipt = ExecutionCommandReceipt(command, acknowledgement, error)
        self.command_receipts[command.command_id] = receipt
        return receipt

    def complete_command(self, command_id: str) -> ResultEnvelope:
        receipt = self.command_receipts[command_id]
        receipt.completed = True
        return receipt.acknowledgement

    def remember_completed_command(
        self,
        command: CommandEnvelope,
        result: ResultEnvelope,
        error: ErrorEnvelope | None = None,
    ) -> None:
        receipt = self.begin_command(command, result, error)
        receipt.completed = True

    def resolve_authoritative_result(
        self,
        result_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        capability_id: str,
        capability_contract_version_id: str,
        normalized_input_digest: str,
    ) -> ResultEnvelope:
        result = next(
            (
                record.result
                for record in self.records.values()
                if record.result and record.result.result_id == result_id
            ),
            None,
        )
        if result is None:
            raise LookupError("authoritative Result is unavailable")
        metadata = result.metadata
        if (
            result.result_status is not ResultStatus.SUCCEEDED
            or result.tenant_id != tenant_id
            or result.workspace_id != workspace_id
            or metadata.get("capability_id") != capability_id
            or metadata.get("capability_contract_version_id") != capability_contract_version_id
            or metadata.get("normalized_input_digest") != normalized_input_digest
            or not isinstance(result.value_reference, str)
        ):
            raise PermissionError("authoritative Result is incompatible or outside scope")
        return result


class SkillDependencyFailure(RuntimeError):
    """Normalized failure raised by approved Skill code."""

    def __init__(
        self,
        message: str,
        *,
        status: ResultStatus = ResultStatus.FAILED,
        category: ErrorCategory = ErrorCategory.DEPENDENCY_FAILURE,
        retry: RetryClassification = RetryClassification.REQUIRES_POLICY_EVALUATION,
        error_code: str = "SKILL_DEPENDENCY_FAILURE",
        ai_invocation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.category = category
        self.retry = retry
        self.error_code = error_code
        self.ai_invocation_id = ai_invocation_id

    @classmethod
    def from_gateway(cls, response: object) -> SkillDependencyFailure:
        error = getattr(response, "error", None)
        result = getattr(response, "result", None)
        if error is None:
            return cls("AI Gateway returned a normalized terminal failure")
        return cls(
            error.message,
            status=getattr(result, "result_status", ResultStatus.FAILED),
            category=error.error_category,
            retry=error.retry_classification,
            error_code=error.error_code,
            ai_invocation_id=getattr(response, "ai_invocation_id", None),
        )


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

        skill_version_id = (
            command.metadata.skill_version_id
            if command.command_version in {"2", "2.0"}
            else self._payload_string(command.payload, "skill_version_id")
        )
        if not skill_version_id:
            return self._reject(command, "SKILL_CONTEXT_INVALID", "SkillVersionId is required")
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
        binding_validator = getattr(implementation, "validate_registry_binding", None)
        if callable(binding_validator):
            try:
                binding_validator(capability)
            except (LookupError, ValueError):
                return self._reject(
                    command,
                    "CAPABILITY_PACKAGE_BINDING_MISMATCH",
                    "Capability Registry and immutable package/schema evidence disagree",
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
            payload={
                key: value
                for key, value in command.payload.items()
                if key not in {"skill_version_id", "timeout_seconds"}
            },
            authoritative_result_id=command.metadata.authoritative_result_id,
        )
        try:
            if skill_input.authoritative_result_id is not None:
                self._authorizer.require(
                    command.metadata.authorization,
                    permission="result.read",
                    tenant_id=command.tenant_id,
                    workspace_id=command.workspace_id,
                )
                statement = self._payload_string(skill_input.payload, "statement").strip()
                source = self._repository.resolve_authoritative_result(
                    skill_input.authoritative_result_id,
                    tenant_id=command.tenant_id,
                    workspace_id=command.workspace_id,
                    capability_id=definition.capability_id,
                    capability_contract_version_id=definition.capability_contract_version_id,
                    normalized_input_digest=hashlib.sha256(statement.encode()).hexdigest(),
                )
                reuse_validator = getattr(implementation, "validate_reused_output", None)
                if not callable(reuse_validator):
                    raise ValueError("Capability cannot validate authoritative reused output")
                validator = cast(Callable[[str], str], reuse_validator)
                assert source.value_reference is not None
                reused_value = validator(source.value_reference)
                output = SkillOutput(reused_value, "", "", source.result_id)
            else:
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
                status=failure.status,
                subject=command.execution_id,
                producer=self.component_name,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
                command_id=command.command_id,
                error_code=failure.error_code,
                category=failure.category,
                severity=ErrorSeverity.WARNING,
                retry=failure.retry,
                message=str(failure),
                metadata=(
                    {"ai_invocation_id": failure.ai_invocation_id}
                    if failure.ai_invocation_id
                    else {"ai_invocation_id_status": "not_created"}
                ),
                predecessor_result_id=acknowledgement.result_id,
            )
            if failure.status is ResultStatus.TIMED_OUT:
                record.state = ExecutionState.TIMED_OUT
                event_type = "ExecutionAttemptTimedOut"
            elif failure.status is ResultStatus.CANCELLED:
                record.state = ExecutionState.CANCELLED
                event_type = "ExecutionAttemptFailed"
            else:
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
                    "capability_id": definition.capability_id,
                    "capability_contract_version_id": definition.capability_contract_version_id,
                    "normalized_input_digest": (
                        hashlib.sha256(
                            self._payload_string(skill_input.payload, "statement").strip().encode()
                        ).hexdigest()
                        if definition.capability_id == "StructuredTaskKindClassification"
                        else ""
                    ),
                    "reused_result_id": output.reused_result_id or "",
                    "avoided_model_calls": 1 if output.reused_result_id else 0,
                    "avoided_input_tokens": 256 if output.reused_result_id else 0,
                    "avoided_output_tokens": 16 if output.reused_result_id else 0,
                    "avoided_cost": "0.01" if output.reused_result_id else "0",
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
        result, error = self._outcomes.unsuccessful(
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
        self._repository.remember_completed_command(command, result, error)
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
        structured = command.payload.get("skill_version_id") == "structured-task-kind-skill-v1"
        raw_invocation_id = result.metadata.get("ai_invocation_id")
        ai_invocation_id = raw_invocation_id if isinstance(raw_invocation_id, str) else None
        context = ObservabilityContext(
            component_identity=self.component_name,
            operation_name="execute_attempt",
            contract_version="1.0",
            observed_at=self._clock.now(),
            environment_identity="local",
            deployment_identity="reference",
            data_classification=(
                DataClassification.INTERNAL if structured else DataClassification.NON_SENSITIVE
            ),
            redaction_status=RedactionStatus.APPLIED
            if structured
            else RedactionStatus.NOT_REQUIRED,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            request_id=command.metadata.request_id,
            command_id=command.command_id,
            workflow_id=command.workflow_id,
            workflow_step_id=command.workflow_step_id,
            execution_id=command.execution_id,
            ai_invocation_id=ai_invocation_id or None,
            result_id=result.result_id,
            error_id=result.error_id,
        )
        attributes: dict[str, object] = {}
        if structured:
            reused_result_id = result.metadata.get("reused_result_id")
            bypassed = (
                result.result_status is ResultStatus.SUCCEEDED
                and isinstance(reused_result_id, str)
                and bool(reused_result_id)
            )
            invoked = ai_invocation_id is not None
            count_status = "canonical_store" if invoked else "not_applicable"
            attributes = {
                "capability_id": "StructuredTaskKindClassification",
                "capability_contract_version_id": "1",
                "prompt_package_ref": "structured-task-kind",
                "prompt_package_version_ref": "v1",
                "disposition": "bypassed" if bypassed else "invoked" if invoked else "not_invoked",
                "terminal_outcome": result.result_status.value,
                "accounting_correlation": (
                    "not_applicable"
                    if bypassed
                    else "ai_invocation_id"
                    if invoked
                    else "not_created"
                ),
                "bypass_reason": "authoritative_result_reuse" if bypassed else "not_applicable",
                "avoided_input_tokens": 256 if bypassed else 0,
                "avoided_output_tokens": 16 if bypassed else 0,
                "avoided_cost": "0.01" if bypassed else "0",
                "provider_attempt_count_status": count_status,
                "repair_attempt_count_status": count_status,
                "fallback_attempt_count_status": count_status,
                "total_model_call_count_status": count_status,
            }
        self._observations.record_log(
            context=context,
            severity=(
                LogSeverity.INFO
                if result.result_status is ResultStatus.SUCCEEDED
                else LogSeverity.ERROR
            ),
            message=f"Execution attempt reached {result.result_status.value}.",
            attributes=attributes,
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
