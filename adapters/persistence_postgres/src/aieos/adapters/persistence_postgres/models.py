"""Private SQLAlchemy persistence types; runtime ports never expose these rows."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Scoped:
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)


class WorkflowRow(Scoped, Base):
    __tablename__ = "workflows"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowStepRow(Scoped, Base):
    __tablename__ = "workflow_steps"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_id"),
            ("workflows.tenant_id", "workflows.workspace_id", "workflows.workflow_id"),
        ),
        UniqueConstraint("tenant_id", "workspace_id", "workflow_id", "attempt_number"),
    )


class ExecutionRow(Scoped, Base):
    __tablename__ = "executions"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_execution_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_id"),
            ("workflows.tenant_id", "workflows.workspace_id", "workflows.workflow_id"),
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_step_id"),
            (
                "workflow_steps.tenant_id",
                "workflow_steps.workspace_id",
                "workflow_steps.workflow_step_id",
            ),
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "previous_execution_id"),
            ("executions.tenant_id", "executions.workspace_id", "executions.execution_id"),
        ),
        UniqueConstraint("tenant_id", "workspace_id", "workflow_step_id", "attempt_number"),
    )


class CommandIdempotencyRow(Scoped, Base):
    __tablename__ = "command_idempotency"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_component: Mapped[str] = mapped_column(String(128), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    command_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class OutcomeRow(Scoped, Base):
    __tablename__ = "outcomes"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_component: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        Index(
            "uq_authoritative_terminal_outcome",
            "tenant_id",
            "workspace_id",
            "owner_component",
            "subject_id",
            unique=True,
            postgresql_where=text("terminal"),
        ),
    )


class OutboxEventRow(Scoped, Base):
    __tablename__ = "outbox_events"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    producer: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_outbox_claim", "available_at", "lease_expires_at"),)


class DeliveryReceiptRow(Base):
    __tablename__ = "delivery_receipts"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "event_id"),
            (
                "outbox_events.tenant_id",
                "outbox_events.workspace_id",
                "outbox_events.event_id",
            ),
        ),
    )


class DecisionEvidenceRow(Scoped, Base):
    __tablename__ = "decision_evidence"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_type: Mapped[str] = mapped_column(String(128), nullable=False)
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    triggering_id: Mapped[str | None] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class MemoryRecordRow(Scoped, Base):
    __tablename__ = "memory_records"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (CheckConstraint("version > 0"),)
