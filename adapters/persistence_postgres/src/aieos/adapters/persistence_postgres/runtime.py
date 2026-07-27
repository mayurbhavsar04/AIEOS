"""Durable repositories for the executable reference runtime.

The public runtime remains deliberately persistence-agnostic.  These adapters
retain its domain objects as JSON snapshots while projecting the relational
identity and lineage columns used for constraints and operational queries.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from pydantic import TypeAdapter
from sqlalchemy import select
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


async def _idempotency(
    session: AsyncSession,
    *,
    target: str,
    command: CommandEnvelope,
    completed: bool,
    outcome_id: str | None,
    payload: bytes,
) -> None:
    command_hash = sha256(_COMMAND.dump_json(command)).hexdigest()
    statement = insert(CommandIdempotencyRow).values(
        tenant_id=command.tenant_id,
        workspace_id=command.workspace_id,
        target_component=target,
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
            "command_id",
        ],
        set_={
            "completed": completed,
            "outcome_id": outcome_id,
            "payload": payload,
        },
        where=CommandIdempotencyRow.command_hash == command_hash,
    )
    await session.execute(statement)
    stored = await session.get(
        CommandIdempotencyRow,
        (
            command.tenant_id,
            command.workspace_id,
            target,
            command.command_id,
        ),
    )
    if stored is None or stored.command_hash != command_hash:
        raise ValueError("CommandId cannot be reused with changed immutable content")


class _Prepared:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
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

    def __init__(self, database: PostgresDatabase) -> None:
        InMemoryWorkflowRepository.__init__(self)
        _Prepared.__init__(self, database)

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            for payload in await session.scalars(select(WorkflowRow.payload)):
                instance = _WORKFLOW.validate_json(payload)
                self.instances.setdefault(instance.workflow_id, instance)
            receipts = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.target_component == "Workflow Engine"
                )
            )
            for payload in receipts:
                receipt = _WORKFLOW_RECEIPT.validate_json(payload)
                self.command_receipts.setdefault(receipt.command.command_id, receipt)

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        for instance in self.instances.values():
            await session.execute(
                insert(WorkflowRow)
                .values(
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                    workflow_id=instance.workflow_id,
                    state=instance.state.value,
                    version=1,
                    correlation_id=instance.correlation_id,
                    payload=_WORKFLOW.dump_json(instance),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "workflow_id"],
                    set_={
                        "state": instance.state.value,
                        "payload": _WORKFLOW.dump_json(instance),
                    },
                )
            )
            await session.execute(
                insert(WorkflowStepRow)
                .values(
                    tenant_id=instance.tenant_id,
                    workspace_id=instance.workspace_id,
                    workflow_step_id=instance.workflow_step_id,
                    workflow_id=instance.workflow_id,
                    state=instance.state.value,
                    attempt_number=instance.attempt_number,
                    payload=_WORKFLOW.dump_json(instance),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "workflow_step_id"],
                    set_={
                        "state": instance.state.value,
                        "attempt_number": instance.attempt_number,
                        "payload": _WORKFLOW.dump_json(instance),
                    },
                )
            )
            if instance.outcome is not None:
                await _immutable_outcome(session, instance.outcome, owner="Workflow Engine")
        for receipt in self.command_receipts.values():
            await _idempotency(
                session,
                target="Workflow Engine",
                command=receipt.command,
                completed=receipt.state.value == "Completed",
                outcome_id=receipt.result.result_id,
                payload=_WORKFLOW_RECEIPT.dump_json(receipt),
            )
            await _immutable_outcome(session, receipt.result, owner="Workflow Engine")

    def interrupted(self) -> tuple[WorkflowInstance, ...]:
        return tuple(item for item in self.instances.values() if item.outcome is None)


class PostgresExecutionRepository(_Prepared, InMemoryExecutionRepository):
    """Durable execution attempts, lineage, immutable outcomes, and receipts."""

    def __init__(self, database: PostgresDatabase) -> None:
        InMemoryExecutionRepository.__init__(self)
        _Prepared.__init__(self, database)

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            for payload in await session.scalars(select(ExecutionRow.payload)):
                record = _EXECUTION.validate_json(payload)
                self.records.setdefault(record.execution_id, record)
            receipts = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.target_component == "Skill Runtime"
                )
            )
            for payload in receipts:
                receipt = _EXECUTION_RECEIPT.validate_json(payload)
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
                encoded = _ERROR.dump_json(record.error)
                statement = insert(OutcomeRow).values(
                    tenant_id=record.error.tenant_id,
                    workspace_id=record.error.workspace_id,
                    outcome_id=record.error.error_id,
                    owner_component="Skill Runtime",
                    subject_id=record.error.affected_subject,
                    kind="Error",
                    terminal=False,
                    payload=encoded,
                    recorded_at=record.error.occurred_at,
                )
                await session.execute(
                    statement.on_conflict_do_nothing(
                        index_elements=["tenant_id", "workspace_id", "outcome_id"]
                    )
                )
                stored = await session.get(
                    OutcomeRow,
                    (
                        record.error.tenant_id,
                        record.error.workspace_id,
                        record.error.error_id,
                    ),
                )
                if stored is None or stored.payload != encoded:
                    raise ValueError("ErrorId cannot be reused with changed immutable content")
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


@dataclass(frozen=True, slots=True)
class _ManagerReceipt:
    command: CommandEnvelope
    workflow_command: CommandEnvelope | None
    result: ResultEnvelope | None


_MANAGER_RECEIPT = TypeAdapter(_ManagerReceipt)


class PostgresRequestRepository(_Prepared, InMemoryRequestRepository):
    """Durable Manager target-owned command idempotency."""

    def __init__(self, database: PostgresDatabase) -> None:
        InMemoryRequestRepository.__init__(self)
        _Prepared.__init__(self, database)

    async def _load(self) -> None:
        async with self._database.transaction() as session:
            rows = await session.scalars(
                select(CommandIdempotencyRow.payload).where(
                    CommandIdempotencyRow.target_component == "Manager"
                )
            )
            for payload in rows:
                receipt = _MANAGER_RECEIPT.validate_json(payload)
                self.commands.setdefault(receipt.command.command_id, receipt.command)
                if receipt.workflow_command is not None:
                    self.workflow_commands.setdefault(
                        receipt.command.command_id, receipt.workflow_command
                    )
                if receipt.result is not None:
                    self.command_results.setdefault(receipt.command.command_id, receipt.result)

    async def flush_in_transaction(self, session: AsyncSession) -> None:
        for command_id, command in self.commands.items():
            result = self.command_results.get(command_id)
            receipt = _ManagerReceipt(command, self.workflow_commands.get(command_id), result)
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


class PostgresDecisionEvidenceRepository(_Prepared, InMemoryDecisionEvidenceRepository):
    """Durable immutable decision evidence with restart-safe lookup."""

    def __init__(self, database: PostgresDatabase) -> None:
        InMemoryDecisionEvidenceRepository.__init__(self)
        _Prepared.__init__(self, database)

    async def _load(self) -> None:
        pending = dict(self.decisions)
        async with self._database.transaction() as session:
            for payload in await session.scalars(select(DecisionEvidenceRow.payload)):
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


PrepareCallback = Callable[[], Awaitable[None]]


__all__ = (
    "PostgresDecisionEvidenceRepository",
    "PostgresExecutionRepository",
    "PostgresRequestRepository",
    "PostgresWorkflowRepository",
    "TransactionParticipant",
    "checkpoint",
)
