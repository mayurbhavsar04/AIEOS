"""Add durable AI Gateway execution ownership and terminal intent recovery."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0003"
down_revision: str | None = "20260810_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_gateway_invocations", sa.Column("execution_owner", sa.String(128), nullable=True)
    )
    op.add_column(
        "ai_gateway_invocations",
        sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_gateway_invocations",
        sa.Column("claim_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "ai_gateway_invocations",
        sa.Column("recovery_phase", sa.String(32), server_default="accepted", nullable=False),
    )
    op.add_column(
        "ai_gateway_invocations", sa.Column("terminal_intent_payload", sa.Text(), nullable=True)
    )
    op.add_column("ai_gateway_attempts", sa.Column("result_payload", sa.Text(), nullable=True))
    op.create_index(
        "ix_ai_gateway_execution_claim",
        "ai_gateway_invocations",
        ["state", "execution_lease_expires_at", "recovery_phase"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_gateway_execution_claim", table_name="ai_gateway_invocations")
    op.drop_column("ai_gateway_attempts", "result_payload")
    op.drop_column("ai_gateway_invocations", "terminal_intent_payload")
    op.drop_column("ai_gateway_invocations", "recovery_phase")
    op.drop_column("ai_gateway_invocations", "claim_generation")
    op.drop_column("ai_gateway_invocations", "execution_lease_expires_at")
    op.drop_column("ai_gateway_invocations", "execution_owner")
