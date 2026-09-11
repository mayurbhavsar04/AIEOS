"""Retain M7 receipt associations, owner evidence, and protected conflicting observations.

Existing incomplete evidence is not backfilled with invented commands or proof.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0007"
down_revision = "20260903_0006"
branch_labels = depends_on = None


def upgrade():
    op.add_column("employee_manager_receipts", sa.Column("recovery_blocked", sa.LargeBinary))
    op.create_unique_constraint(
        "uq_employee_execution_receipt",
        "employee_manager_receipts",
        ["tenant_id", "workspace_id", "employee_execution_id"],
    )
    op.add_column(
        "employee_start_workflow_commands", sa.Column("reference_evidence", sa.LargeBinary)
    )
    op.add_column("employee_start_workflow_commands", sa.Column("command_profile", sa.String(64)))
    op.add_column("employee_manager_receipts", sa.Column("start_command_id", sa.String(128)))
    op.add_column("employee_manager_receipts", sa.Column("decision_evidence", sa.LargeBinary))
    op.add_column(
        "employee_manager_receipts",
        sa.Column("observation_progress", sa.String(32), nullable=False, server_default="NotDue"),
    )
    op.create_unique_constraint(
        "uq_employee_receipt_start",
        "employee_manager_receipts",
        ["tenant_id", "workspace_id", "start_command_id"],
    )
    op.create_foreign_key(
        "fk_employee_receipt_start",
        "employee_manager_receipts",
        "employee_start_workflow_commands",
        ["tenant_id", "workspace_id", "start_command_id"],
        ["tenant_id", "workspace_id", "command_id"],
    )
    op.create_table(
        "employee_durable_references",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column("owning_contract", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(128), primary_key=True),
        sa.Column("identity", sa.String(128), primary_key=True),
        sa.Column("immutable_pin", sa.String(256), primary_key=True),
        sa.Column("evidence", sa.LargeBinary, nullable=False),
    )
    op.create_table(
        "employee_observation_conflicts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("source_identity", sa.String(256), nullable=False),
        sa.Column("existing_evidence", sa.LargeBinary, nullable=False),
        sa.Column("incoming_evidence", sa.LargeBinary, nullable=False),
    )


def downgrade():
    op.drop_column("employee_manager_receipts", "recovery_blocked")
    op.drop_constraint("uq_employee_execution_receipt", "employee_manager_receipts", type_="unique")
    op.drop_column("employee_start_workflow_commands", "reference_evidence")
    op.drop_table("employee_observation_conflicts")
    op.drop_table("employee_durable_references")
    op.drop_constraint("fk_employee_receipt_start", "employee_manager_receipts", type_="foreignkey")
    op.drop_constraint("uq_employee_receipt_start", "employee_manager_receipts", type_="unique")
    for name in ("observation_progress", "decision_evidence", "start_command_id"):
        op.drop_column("employee_manager_receipts", name)
    op.drop_column("employee_start_workflow_commands", "command_profile")
