"""remediation_actions: add target_backend_start (Phase 2 stronger recheck)

Stored as epoch seconds (Float), not a timestamp column -- see the column's docstring
in app/models/remediations.py for why: a raw datetime isn't natively JSON-serializable
over the MCP round-trip, the same class of bug that broke target_duration_seconds
before its ::float8 cast was added.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remediation_actions", sa.Column("target_backend_start", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("remediation_actions", "target_backend_start")
