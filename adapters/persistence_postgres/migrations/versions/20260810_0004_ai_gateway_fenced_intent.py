"""Fence AI Gateway terminal intents to their authorizing execution generation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0004"
down_revision: str | None = "20260810_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_gateway_invocations",
        sa.Column("terminal_intent_owner", sa.String(128), nullable=True),
    )
    op.add_column(
        "ai_gateway_invocations",
        sa.Column("terminal_intent_generation", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_gateway_invocations", "terminal_intent_generation")
    op.drop_column("ai_gateway_invocations", "terminal_intent_owner")
