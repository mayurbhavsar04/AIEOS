"""Durable repositories for the executable reference runtime.

The public runtime remains deliberately persistence-agnostic.  These adapters
retain its domain objects as JSON snapshots while projecting the relational
identity and lineage columns used for constraints and operational queries.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, cast

from pydantic import TypeAdapter
from sqlalchemy import and_, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aieos.contracts import ErrorEnvelope, ResultEnvelope
from aieos.contracts.commands import CommandEnvelope
from aieos.domain import DecisionEvidence, InMemoryDecisionEvidenceRepository
from aieos.manager import InMemoryRequestRepository
from aieos.skill_runtime import ExecutionRecord, InMemoryExecutionRepository
from aieos.skill_runtime.runtime import ExecutionCommandReceipt
from aieos.workflow_engine import InMemoryWorkflowRepository, WorkflowInstance
from aieos.workflow_engine.engine import WorkflowCommandReceipt

from .database import PostgresDatabase
from .models import (
    CommandIdempotencyRow,
    DecisionEvidenceRow,
    ExecutionRow,
    OutcomeRow,
    WorkflowRow,
    WorkflowStepRow,
)


class TransactionParticipant(Protocol):
    async def prepare(self) -> None: ...

    async def flush_in_transaction(self, session: AsyncSession) -> None: ...


_COMMAND = TypeAdapter(CommandEnvelope)
_RESULT = TypeAdapter(ResultEnvelope)
_ERROR = TypeAdapter(ErrorEnvelope)
_WORKFLOW = TypeAdapter(WorkflowInstance)
_WORKFLOW_RECEIPT = TypeAdapter(WorkflowCommandReceipt)
_EXECUTION = TypeAdapter(ExecutionRecord)
_EXECUTION_RECEIPT = TypeAdapter(ExecutionCommandReceipt)
_DECISION = TypeAdapter(DecisionEvidence)


def _normalize_result(result: ResultEnvelope) -> ResultEnvelope:
    metadata = dict(result.metadata)
    execution_ids = metadata.get("execution_ids")
    if isinstance(execution_ids, list):
        metadata["execution_ids"] = tuple(cast(list[str], execution_ids))
    return replace(result, metadata=metadata)


def command_intent_hash(command: CommandEnvelope) -> str:
    """Hash immutable target-owned intent, excluding delivery identities."""
    authorization = command.metadata.authorization
    document = {
        "command_type": command.command_type,
        "command_version": command.command_version,
        "target_component": command.target_component,
        "initiator": command.initiator,
        "tenant_id": command.tenant_id,
        "workspace_id": command.workspace_id,
        "payload": command.payload,
        "workflow_id": command.workflow_id,
        "workflow_step_id": command.workflow_step_id,
        "execution_id": command.execution_id,
        "attempt_number": command.metadata.attempt_number,
        "authorization": {
            "actor_id": authorization.actor_id,
            "permissions": sorted(authorization.permissions),
            "tenant_id": authorization.tenant_id,
            "workspace_id": authorization.workspace_id,
            "policy_id": authorization.policy_id,
            "policy_version_id": authorization.policy_version_id,
        },
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return sha256(canonical).hexdigest()


def scoped_idempotency_lock_key(command: CommandEnvelope) -> str:
    return "\x1f".join(
        (
            command.tenant_id,
            command.workspace_id,
            command.target_component,
            command.metadata.idempotency_key,
        )
    )


def scoped_workflow_lock_key(command: CommandEnvelope) -> str | None:
    """Return the governed exact-Workflow serialization key when one exists."""
    if command.workflow_id is None:
        return None
    return workflow_lock_key(command.tenant_id, command.workspace_id, command.workflow_id)


def workflow_lock_key(tenant_id: str, workspace_id: str, workflow_id: str) -> str:
    """Return the shared exact-Workflow mutation serialization authority."""
    return "\x1f".join(("WorkflowAdmission", tenant_id, workspace_id, workflow_id))


async def _immutable_outcome(
    session: AsyncSession,
    result: ResultEnvelope,
    *,
    owner: str,
) -> None:
    encoded = _RESULT.dump_json(result)
    terminal = result.completed_at is not None
    statement = insert(OutcomeRow).values(
        tenant_id=result.tenant_id,
        workspace_id=result.workspace_id,
        outcome_id=result.result_id,
        owner_component=owner,
        subject_id=result.subject_reference,
        kind="Result",
        terminal=terminal,
        payload=encoded,
        recorded_at=result.completed_at or result.started_at or datetime.now(UTC),
    )
    await session.execute(
        statement.on_conflict_do_nothing(index_elements=["tenant_id", "workspace_id", "outcome_id"])
    )
    stored = await session.get(
        OutcomeRow, (result.tenant_id, result.workspace_id, result.result_id)
    )
    if stored is None or stored.payload != encoded:
        raise ValueError("ResultId cannot be reused with changed immutable content")


async def _immutable_error(
    session: AsyncSession,
    error: ErrorEnvelope,
    *,
    owner: str,
) -> None:
    encoded = _ERROR.dump_json(error)
    statement = insert(OutcomeRow).values(
        tenant_id=error.tenant_id,
        workspace_id=error.workspace_id,
        outcome_id=error.error_id,
        owner_component=owner,
        subject_id=error.affected_subject,
        kind="Error",
        terminal=False,
        payload=encoded,
        recorded_at=error.occurred_at,
    )
    await session.execute(
        statement.on_conflict_do_nothing(index_elements=["tenant_id", "workspace_id", "outcome_id"])
    )
    stored = await session.get(
        OutcomeRow,
        (error.tenant_id, error.workspace_id, error.error_id),
    )
    if stored is None or stored.payload != encoded:
        raise ValueError("ErrorId cannot be reused with changed immutable content")


async def _idempotency(
    session: AsyncSession,
    *,
    target: str,
    command: CommandEnvelope,
    completed: bool,
    outcome_id: str | None,
    payload: bytes,
) -> None:
    command_hash = command_intent_hash(command)
    statement = insert(CommandIdempotencyRow).values(
        tenant_id=command.tenant_id,
        workspace_id=command.workspace_id,
        target_component=target,
        idempotency_key=command.metadata.idempotency_key,
        command_id=command.command_id,
        command_hash=command_hash,
        completed=completed,
        outcome_id=outcome_id,
        payload=payload,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[
            "tenant_id",
            "workspace_id",
            "target_component",
            "idempotency_key",
        ],
        set_={
            "completed": completed,
            "outcome_id": outcome_id,
            "payload": payload,
        },
        where=and_(
            CommandIdempotencyRow.command_hash == command_hash,
            or_(CommandIdempotencyRow.completed.is_(False), literal(completed)),
        ),
    )
    await session.execute(statement)
    stored = await session.get(
        CommandIdempotencyRow,
        (
            command.tenant_id,
            command.workspace_id,
            target,
            command.metadata.idempotency_key,
        ),
    )
    if stored is None or stored.command_hash != command_hash:
        raise ValueError("IdempotencyKey cannot be reused with changed immutable intent")


class _Prepared:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        self._database = database
        self._tenant_id = tenant_id
        self._workspace_id = workspace_id
        self._prepared = False

    async def prepare(self) -> None:
        if self._prepared:
            return
        await self._load()
        self._prepared = True

    async def _load(self) -> None:
        raise NotImplementedError


class PostgresWorkflowRepository(_Prepared, InMemoryWorkflowRepository):
    """Durable workflow, step, lineage, outcome, and Workflow Engine receipts."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        InMemoryWorkflowRepository.__init__(self)
        _Prepared.__init__(
            self,
            database,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        self._workflow_versions: dict[str, int] = {}
        self._workflow_baseline: dict[str, bytes] = {}
        self._receipt_baseline: dict[str, bytes] = {}
        self._flushed_workflows: dict[str, tuple[int, bytes]] = {}
        self._flushed_receipts: dict[str, bytes] = {}

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            for row in await session.scalars(
                select(WorkflowRow).where(
                    WorkflowRow.tenant_id == self._tenant_id,
                    WorkflowRow.workspace_id == self._workspace_id,
                )
            ):
                instance = _WORKFLOW.validate_json(row.payload)
                if instance.outcome is not None:
                    instance.outcome = _normalize_result(instance.outcome)
                self.instances[instance.workflow_id] = instance
                self._workflow_versions[instance.workflow_id] = row.version
                self._workflow_baseline[instance.workflow_id] = row.payload
            receipts = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.tenant_id == self._tenant_id,
                    CommandIdempotencyRow.workspace_id == self._workspace_id,
                    CommandIdempotencyRow.target_component == "Workflow Engine",
                )
            )
            for payload in receipts:
                receipt = _WORKFLOW_RECEIPT.validate_json(payload)
                receipt.result = _normalize_result(receipt.result)
                self.command_receipts[receipt.command.command_id] = receipt
                self._receipt_baseline[receipt.command.command_id] = payload

    async def refresh_workflow(self, workflow_id: str) -> WorkflowInstance | None:
        """Replace a warmed worker's cached Workflow with durable authority."""
        async with self._database.transaction() as session:
            row = await session.get(
                WorkflowRow,
                (self._tenant_id, self._workspace_id, workflow_id),
            )
        if row is None:
            self.instances.pop(workflow_id, None)
            return None
        instance = _WORKFLOW.validate_json(row.payload)
        if instance.outcome is not None:
            instance.outcome = _normalize_result(instance.outcome)
        self.instances[workflow_id] = instance
        self._workflow_versions[workflow_id] = row.version
        self._workflow_baseline[workflow_id] = row.payload
        return instance

    async def replay_command(
        self, command: CommandEnvelope
    ) -> tuple[CommandEnvelope, ResultEnvelope | None] | None:
        """Resolve authoritative scoped replay before creating a WorkflowId."""
        async with self._database.transaction() as session:
            row = await session.get(
                CommandIdempotencyRow,
                (
                    command.tenant_id,
                    command.workspace_id,
                    "Workflow Engine",
                    command.metadata.idempotency_key,
                ),
                with_for_update=True,
            )
            if row is None:
                return None
            if row.command_hash != command_intent_hash(command):
                raise ValueError("IdempotencyKey cannot be reused with changed immutable intent")
            receipt = _WORKFLOW_RECEIPT.validate_json(row.payload)
            completed = row.completed
        receipt.result = _normalize_result(receipt.result)
        await self.refresh_workflow(receipt.workflow_id)
        self.command_receipts[receipt.command.command_id] = receipt
        return receipt.command, receipt.result if completed else None

    async def authoritative_ai_admission(
        self,
        *,
        workflow_id: str,
        command_id: str,
        execution_id: str,
    ) -> Mapping[str, object] | None:
        """Resolve fresh committed Workflow authority from PostgreSQL."""
        if await self.refresh_workflow(workflow_id) is None:
            return None
        return await super().authoritative_ai_admission(
            workflow_id=workflow_id,
            command_id=command_id,
            execution_id=execution_id,
        )

    async def owns_ai_dispatch(
        self,
        *,
        workflow_id: str,
        command_id: str,
        execution_id: str,
    ) -> bool:
        """Resolve fresh target-owned Workflow dispatch authority from PostgreSQL."""
        if await self.refresh_workflow(workflow_id) is None:
            return False
        return await super().owns_ai_dispatch(
            workflow_id=workflow_id,
            command_id=command_id,
            execution_id=execution_id,
        )

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        for instance in self.instances.values():
            encoded = _WORKFLOW.dump_json(instance)
            if self._workflow_baseline.get(instance.workflow_id) == encoded:
                continue
            expected_version = self._workflow_versions.get(instance.workflow_id, 0)
            next_version = expected_version + 1
            result = await session.execute(
                insert(WorkflowRow)
                .values(
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                    workflow_id=instance.workflow_id,
                    state=instance.state.value,
                    version=next_version,
                    correlation_id=instance.correlation_id,
                    payload=_WORKFLOW.dump_json(instance),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "workflow_id"],
                    set_={
                        "state": instance.state.value,
                        "version": next_version,
                        "payload": encoded,
                    },
                    where=WorkflowRow.version == expected_version,
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("stale Workflow snapshot rejected by durable version fence")
            await session.execute(
                insert(WorkflowStepRow)
                .values(
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                    workflow_step_id=instance.workflow_step_id,
                    workflow_id=instance.workflow_id,
                    state=instance.state.value,
                    attempt_number=instance.attempt_number,
                    payload=encoded,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "workflow_step_id"],
                    set_={
                        "state": instance.state.value,
                        "attempt_number": instance.attempt_number,
                        "payload": encoded,
                    },
                )
            )
            if instance.outcome is not None:
                await _immutable_outcome(session, instance.outcome, owner="Workflow Engine")
            if instance.error is not None:
                await _immutable_error(session, instance.error, owner="Workflow Engine")
            self._flushed_workflows[instance.workflow_id] = (next_version, encoded)
        for receipt in self.command_receipts.values():
            encoded_receipt = _WORKFLOW_RECEIPT.dump_json(receipt)
            if self._receipt_baseline.get(receipt.command.command_id) == encoded_receipt:
                continue
            await _idempotency(
                session,
                target="Workflow Engine",
                command=receipt.command,
                completed=receipt.state.value == "Completed",
                outcome_id=receipt.result.result_id,
                payload=encoded_receipt,
            )
            await _immutable_outcome(session, receipt.result, owner="Workflow Engine")
            if receipt.error is not None:
                await _immutable_error(session, receipt.error, owner="Workflow Engine")
            self._flushed_receipts[receipt.command.command_id] = encoded_receipt

    def checkpoint_completed(self) -> None:
        for workflow_id, (version, payload) in self._flushed_workflows.items():
            self._workflow_versions[workflow_id] = version
            self._workflow_baseline[workflow_id] = payload
        self._receipt_baseline.update(self._flushed_receipts)
        self._flushed_workflows.clear()
        self._flushed_receipts.clear()

    def interrupted(self) -> tuple[WorkflowInstance, ...]:
        return tuple(item for item in self.instances.values() if item.outcome is None)


class PostgresExecutionRepository(_Prepared, InMemoryExecutionRepository):
    """Durable execution attempts, lineage, immutable outcomes, and receipts."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        InMemoryExecutionRepository.__init__(self)
        _Prepared.__init__(
            self,
            database,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            for payload in await session.scalars(
                select(ExecutionRow.payload).where(
                    ExecutionRow.tenant_id == self._tenant_id,
                    ExecutionRow.workspace_id == self._workspace_id,
                )
            ):
                record = _EXECUTION.validate_json(payload)
                record.acknowledgement = _normalize_result(record.acknowledgement)
                if record.result is not None:
                    record.result = _normalize_result(record.result)
                self.records.setdefault(record.execution_id, record)
            receipts = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.tenant_id == self._tenant_id,
                    CommandIdempotencyRow.workspace_id == self._workspace_id,
                    CommandIdempotencyRow.target_component == "Skill Runtime",
                )
            )
            for payload in receipts:
                receipt = _EXECUTION_RECEIPT.validate_json(payload)
                receipt.acknowledgement = _normalize_result(receipt.acknowledgement)
                self.command_receipts.setdefault(receipt.command.command_id, receipt)

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        ordered = sorted(self.records.values(), key=lambda item: item.attempt_number)
        previous_by_step: dict[str, str] = {}
        for record in ordered:
            command = next(
                (
                    receipt.command
                    for receipt in self.command_receipts.values()
                    if receipt.command.execution_id == record.execution_id
                ),
                None,
            )
            await session.execute(
                insert(ExecutionRow)
                .values(
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    execution_id=record.execution_id,
                    workflow_id=record.workflow_id,
                    workflow_step_id=record.workflow_step_id,
                    attempt_number=record.attempt_number,
                    previous_execution_id=previous_by_step.get(record.workflow_step_id),
                    causation_id=command.causation_id
                    if command is not None
                    else record.execution_id,
                    state=record.state.value,
                    payload=_EXECUTION.dump_json(record),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "execution_id"],
                    set_={"state": record.state.value, "payload": _EXECUTION.dump_json(record)},
                )
            )
            previous_by_step[record.workflow_step_id] = record.execution_id
            if record.result is not None:
                await _immutable_outcome(session, record.result, owner="Skill Runtime")
            if record.error is not None:
                await _immutable_error(session, record.error, owner="Skill Runtime")
        for receipt in self.command_receipts.values():
            await _idempotency(
                session,
                target="Skill Runtime",
                command=receipt.command,
                completed=receipt.completed,
                outcome_id=receipt.acknowledgement.result_id,
                payload=_EXECUTION_RECEIPT.dump_json(receipt),
            )
            await _immutable_outcome(session, receipt.acknowledgement, owner="Skill Runtime")
            if receipt.error is not None:
                await _immutable_error(session, receipt.error, owner="Skill Runtime")


@dataclass(frozen=True, slots=True)
class _ManagerReceipt:
    command: CommandEnvelope
    workflow_command: CommandEnvelope | None
    result: ResultEnvelope | None
    error: ErrorEnvelope | None = None


_MANAGER_RECEIPT = TypeAdapter(_ManagerReceipt)


class PostgresRequestRepository(_Prepared, InMemoryRequestRepository):
    """Durable Manager target-owned command idempotency."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        InMemoryRequestRepository.__init__(self)
        _Prepared.__init__(
            self,
            database,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            rows = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.tenant_id == self._tenant_id,
                    CommandIdempotencyRow.workspace_id == self._workspace_id,
                    CommandIdempotencyRow.target_component == "Manager",
                )
            )
            for payload in rows:
                receipt = _MANAGER_RECEIPT.validate_json(payload)
                if receipt.result is not None:
                    receipt = replace(receipt, result=_normalize_result(receipt.result))
                self.commands.setdefault(receipt.command.command_id, receipt.command)
                if receipt.workflow_command is not None:
                    self.workflow_commands.setdefault(
                        receipt.command.command_id, receipt.workflow_command
                    )
                if receipt.result is not None:
                    self.command_results.setdefault(receipt.command.command_id, receipt.result)
                if receipt.error is not None:
                    self.command_errors.setdefault(receipt.command.command_id, receipt.error)

    async def replay_command(
        self, command: CommandEnvelope
    ) -> tuple[CommandEnvelope, ResultEnvelope | None] | None:
        """Resolve a scoped logical request before target execution.

        The stored command is replayed for unfinished work so a redelivery with a
        new CommandId cannot create a second Workflow.
        """
        async with self._database.transaction() as session:
            row = await session.get(
                CommandIdempotencyRow,
                (
                    command.tenant_id,
                    command.workspace_id,
                    "Manager",
                    command.metadata.idempotency_key,
                ),
                with_for_update=True,
            )
            if row is None:
                return None
            if row.command_hash != command_intent_hash(command):
                raise ValueError("IdempotencyKey cannot be reused with changed immutable intent")
            receipt = _MANAGER_RECEIPT.validate_json(row.payload)
            result = _normalize_result(receipt.result) if receipt.result is not None else None
            return receipt.command, result if row.completed else None

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        for command_id, command in self.commands.items():
            result = self.command_results.get(command_id)
            error = self.command_errors.get(command_id)
            receipt = _ManagerReceipt(
                command,
                self.workflow_commands.get(command_id),
                result,
                error,
            )
            await _idempotency(
                session,
                target="Manager",
                command=command,
                completed=result is not None and result.completed_at is not None,
                outcome_id=result.result_id if result is not None else None,
                payload=_MANAGER_RECEIPT.dump_json(receipt),
            )
            if result is not None:
                await _immutable_outcome(session, result, owner="Manager")
            if error is not None:
                await _immutable_error(session, error, owner="Manager")


class PostgresDecisionEvidenceRepository(_Prepared, InMemoryDecisionEvidenceRepository):
    """Durable immutable decision evidence with restart-safe lookup."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        InMemoryDecisionEvidenceRepository.__init__(self)
        _Prepared.__init__(
            self,
            database,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    async def _load(self) -> None:
        pending = dict(self.decisions)
        async with self._database.transaction() as session:
            for payload in await session.scalars(
                select(DecisionEvidenceRow.payload).where(
                    DecisionEvidenceRow.tenant_id == self._tenant_id,
                    DecisionEvidenceRow.workspace_id == self._workspace_id,
                )
            ):
                evidence = _DECISION.validate_json(payload)
                self.decisions.setdefault(evidence.decision_id, evidence)
        for decision_id, evidence in pending.items():
            existing = self.decisions.get(decision_id)
            if existing is not None and existing != evidence:
                raise ValueError("DecisionId cannot be reused with changed evidence")
            self.decisions[decision_id] = evidence

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        for evidence in self.decisions.values():
            encoded = _DECISION.dump_json(evidence)
            statement = insert(DecisionEvidenceRow).values(
                tenant_id=evidence.tenant_id,
                workspace_id=evidence.workspace_id,
                decision_id=evidence.decision_id,
                decision_type=evidence.decision_type,
                component=evidence.component,
                correlation_id=evidence.correlation_id,
                triggering_id=evidence.triggering_id,
                recorded_at=evidence.recorded_at,
                payload=encoded,
            )
            await session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["tenant_id", "workspace_id", "decision_id"]
                )
            )
            stored = await session.get(
                DecisionEvidenceRow,
                (evidence.tenant_id, evidence.workspace_id, evidence.decision_id),
            )
            if stored is None or stored.payload != encoded:
                raise ValueError("DecisionId cannot be reused with changed evidence")


async def checkpoint(
    database: PostgresDatabase, participants: tuple[TransactionParticipant, ...]
) -> None:
    async with database.transaction() as session:
        for participant in participants:
            await participant.flush_in_transaction(session)
    for participant in participants:
        completed = getattr(participant, "checkpoint_completed", None)
        if callable(completed):
            completed()


PrepareCallback = Callable[[], Awaitable[None]]


__all__ = (
    "PostgresDecisionEvidenceRepository",
    "PostgresExecutionRepository",
    "PostgresRequestRepository",
    "PostgresWorkflowRepository",
    "TransactionParticipant",
    "checkpoint",
)
