"""Create the explicit, scope-safe durable runtime schema."""

import sqlalchemy as sa
from alembic import op

revision = "20260727_0001"
down_revision = None
branch_labels = None
depends_on = None


def _scope() -> tuple[sa.Column[str], sa.Column[str]]:
    return (
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "workflows",
        *_scope(),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "workflow_id"),
    )
    op.create_table(
        "workflow_steps",
        *_scope(),
        sa.Column("workflow_step_id", sa.String(128), nullable=False),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_id"),
            ("workflows.tenant_id", "workflows.workspace_id", "workflows.workflow_id"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "workflow_step_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "workflow_id",
            "attempt_number",
            name="uq_workflow_step_attempt",
        ),
    )
    op.create_table(
        "executions",
        *_scope(),
        sa.Column("execution_id", sa.String(128), nullable=False),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("workflow_step_id", sa.String(128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("previous_execution_id", sa.String(128)),
        sa.Column("causation_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_id"),
            ("workflows.tenant_id", "workflows.workspace_id", "workflows.workflow_id"),
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "workflow_step_id"),
            (
                "workflow_steps.tenant_id",
                "workflow_steps.workspace_id",
                "workflow_steps.workflow_step_id",
            ),
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "previous_execution_id"),
            ("executions.tenant_id", "executions.workspace_id", "executions.execution_id"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "execution_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "workflow_step_id",
            "attempt_number",
            name="uq_execution_attempt",
        ),
    )
    op.create_table(
        "command_idempotency",
        *_scope(),
        sa.Column("target_component", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("command_id", sa.String(128), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("outcome_id", sa.String(128)),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "target_component", "idempotency_key"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "target_component",
            "command_id",
            name="uq_command_identity_per_target",
        ),
    )
    op.create_index(
        "ix_command_idempotency_lookup",
        "command_idempotency",
        ("tenant_id", "workspace_id", "target_component", "idempotency_key"),
    )
    op.create_table(
        "outcomes",
        *_scope(),
        sa.Column("outcome_id", sa.String(128), nullable=False),
        sa.Column("owner_component", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "outcome_id"),
    )
    op.create_index(
        "uq_authoritative_terminal_outcome",
        "outcomes",
        ("tenant_id", "workspace_id", "owner_component", "subject_id"),
        unique=True,
        postgresql_where=sa.text("terminal"),
    )
    op.create_table(
        "outbox_events",
        *_scope(),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("producer", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("required_consumer_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "required_consumer_count >= 0",
            name="ck_outbox_required_consumer_count",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "event_id"),
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox_events",
        ("available_at", "lease_expires_at"),
    )
    op.create_table(
        "delivery_receipts",
        *_scope(),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("consumer_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('Pending', 'Claimed', 'Failed', 'Delivered')",
            name="ck_delivery_receipt_status",
        ),
        sa.CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_delivery_receipt_attempts",
        ),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "event_id"),
            (
                "outbox_events.tenant_id",
                "outbox_events.workspace_id",
                "outbox_events.event_id",
            ),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "event_id", "consumer_name"),
    )
    op.create_index(
        "ix_delivery_receipt_claim",
        "delivery_receipts",
        ("status", "lease_expires_at", "tenant_id", "workspace_id"),
    )
    op.create_table(
        "decision_evidence",
        *_scope(),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("decision_type", sa.String(128), nullable=False),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("triggering_id", sa.String(128)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "decision_id"),
    )
    op.create_table(
        "memory_records",
        *_scope(),
        sa.Column("memory_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("provenance", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("version > 0", name="ck_memory_version_positive"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "memory_id", "version"),
    )


def downgrade() -> None:
    op.drop_table("memory_records")
    op.drop_table("decision_evidence")
    op.drop_index("ix_delivery_receipt_claim", table_name="delivery_receipts")
    op.drop_table("delivery_receipts")
    op.drop_index("ix_outbox_claim", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("uq_authoritative_terminal_outcome", table_name="outcomes")
    op.drop_table("outcomes")
    op.drop_index("ix_command_idempotency_lookup", table_name="command_idempotency")
    op.drop_table("command_idempotency")
    op.drop_table("executions")
    op.drop_table("workflow_steps")
    op.drop_table("workflows")
