"""remediation_actions: HITL remediation Phase 1 (cancel a runaway query)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    remediation_status = sa.Enum(
        "proposed", "approved", "rejected", "executed", "failed", name="remediation_status"
    )

    op.create_table(
        "remediation_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("target_pid", sa.Integer(), nullable=True),
        sa.Column("target_query", sa.Text(), nullable=True),
        sa.Column("target_duration_seconds", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", remediation_status, nullable=False, server_default="proposed"),
        sa.Column("slack_response_url", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
    )
    op.create_index("ix_remediation_actions_incident_id", "remediation_actions", ["incident_id"])
    op.create_index("ix_remediation_actions_status", "remediation_actions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_remediation_actions_status", table_name="remediation_actions")
    op.drop_index("ix_remediation_actions_incident_id", table_name="remediation_actions")
    op.drop_table("remediation_actions")

    sa.Enum(name="remediation_status").drop(op.get_bind(), checkfirst=True)
