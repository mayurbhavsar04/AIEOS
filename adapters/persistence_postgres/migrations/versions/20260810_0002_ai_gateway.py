"""Add durable AI Gateway invocation, accounting, attempt, and cache state."""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def _scope() -> tuple[sa.Column[str], sa.Column[str]]:
    return (
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "ai_gateway_invocations",
        *_scope(),
        sa.Column("ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("intent_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("request_payload", sa.Text(), nullable=False),
        sa.Column("acknowledgement_payload", sa.Text(), nullable=False),
        sa.Column("route_payload", sa.Text()),
        sa.Column("terminal_payload", sa.Text()),
        sa.Column("terminal_result_id", sa.String(128)),
        sa.Column("terminal_error_id", sa.String(128)),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "ai_invocation_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_ai_gateway_scoped_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('Requested','PolicyValidated','ProviderSelected','Prepared',"
            "'Invoked','Streaming','Retrying','Succeeded','Failed','TimedOut','Cancelled')",
            name="ck_ai_gateway_invocation_state",
        ),
    )
    op.create_index(
        "ix_ai_gateway_invocation_recovery",
        "ai_gateway_invocations",
        ("tenant_id", "workspace_id", "state", "updated_at"),
    )
    op.create_index(
        "ix_ai_gateway_invocation_replay",
        "ai_gateway_invocations",
        ("tenant_id", "workspace_id", "idempotency_key", "intent_fingerprint"),
    )
    op.create_table(
        "ai_gateway_budgets",
        *_scope(),
        sa.Column("ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("reserved_amount", sa.Numeric(24, 12), nullable=False),
        sa.Column("actual_amount", sa.Numeric(24, 12)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("usage_payload", sa.Text()),
        sa.Column("pricing_version", sa.String(128)),
        sa.Column("pricing_payload", sa.Text()),
        sa.Column("allocation_payload", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_reason", sa.String(64)),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "ai_invocation_id"),
            (
                "ai_gateway_invocations.tenant_id",
                "ai_gateway_invocations.workspace_id",
                "ai_gateway_invocations.ai_invocation_id",
            ),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "ai_invocation_id"),
        sa.CheckConstraint(
            "state IN ('pending','committed','released','expired','usage_pending')",
            name="ck_ai_gateway_budget_state",
        ),
    )
    op.create_index(
        "ix_ai_gateway_budget_recovery",
        "ai_gateway_budgets",
        ("tenant_id", "workspace_id", "state", "expires_at"),
    )
    op.create_table(
        "ai_gateway_attempts",
        *_scope(),
        sa.Column("ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(128), nullable=False),
        sa.Column("adapter_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("usage_payload", sa.Text()),
        sa.Column("cost_amount", sa.Numeric(24, 12)),
        sa.Column("effect_reference", sa.String(256)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "ai_invocation_id"),
            (
                "ai_gateway_invocations.tenant_id",
                "ai_gateway_invocations.workspace_id",
                "ai_gateway_invocations.ai_invocation_id",
            ),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "ai_invocation_id", "attempt_number"),
        sa.CheckConstraint("attempt_number > 0", name="ck_ai_gateway_attempt_number"),
    )
    op.create_table(
        "ai_gateway_cache",
        *_scope(),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_payload", sa.Text(), nullable=False),
        sa.Column("provenance_ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "cache_key"),
    )
    op.create_index("ix_ai_gateway_cache_expiry", "ai_gateway_cache", ("expires_at",))
    op.create_table(
        "ai_gateway_usage_ledger",
        *_scope(),
        sa.Column("ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("usage_event_key", sa.String(128), nullable=False),
        sa.Column("attempt_number", sa.Integer()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("usage_payload", sa.Text(), nullable=False),
        sa.Column("cost_amount", sa.Numeric(24, 12), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "ai_invocation_id"),
            (
                "ai_gateway_invocations.tenant_id",
                "ai_gateway_invocations.workspace_id",
                "ai_gateway_invocations.ai_invocation_id",
            ),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "ai_invocation_id", "usage_event_key"),
        sa.CheckConstraint("cost_amount >= 0", name="ck_ai_gateway_usage_cost"),
    )
    op.create_index(
        "ix_ai_gateway_usage_recovery",
        "ai_gateway_usage_ledger",
        ("tenant_id", "workspace_id", "ai_invocation_id", "final"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_gateway_usage_recovery", table_name="ai_gateway_usage_ledger")
    op.drop_table("ai_gateway_usage_ledger")
    op.drop_index("ix_ai_gateway_cache_expiry", table_name="ai_gateway_cache")
    op.drop_table("ai_gateway_cache")
    op.drop_table("ai_gateway_attempts")
    op.drop_index("ix_ai_gateway_budget_recovery", table_name="ai_gateway_budgets")
    op.drop_table("ai_gateway_budgets")
    op.drop_index("ix_ai_gateway_invocation_replay", table_name="ai_gateway_invocations")
    op.drop_index("ix_ai_gateway_invocation_recovery", table_name="ai_gateway_invocations")
    op.drop_table("ai_gateway_invocations")
