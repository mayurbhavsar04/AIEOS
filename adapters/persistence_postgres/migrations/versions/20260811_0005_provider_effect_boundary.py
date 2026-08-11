"""Add the process-independent provider-effect boundary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0005"
down_revision: str | None = "20260810_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_gateway_provider_effects",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("effect_key", sa.String(256), nullable=False),
        sa.Column("ai_invocation_id", sa.String(128), nullable=False),
        sa.Column("effect_type", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result_payload", sa.Text(), nullable=True),
        sa.Column("dispatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatching_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('reserved','dispatching','completed','failed','ambiguous')",
            name="ck_ai_gateway_provider_effect_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "ai_invocation_id"],
            [
                "ai_gateway_invocations.tenant_id",
                "ai_gateway_invocations.workspace_id",
                "ai_gateway_invocations.ai_invocation_id",
            ],
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "effect_key"),
    )


def downgrade() -> None:
    op.drop_table("ai_gateway_provider_effects")
