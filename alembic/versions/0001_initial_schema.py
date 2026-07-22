"""initial schema: pgvector extension, raw_events, incidents

Revision ID: 0001
Revises:
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    event_source = sa.Enum("cloudwatch", "github_actions", name="event_source")
    incident_status = sa.Enum(
        "open", "diagnosing", "awaiting_approval", "resolved", "rejected", name="incident_status"
    )
    risk_tier = sa.Enum("low", "medium", "high", name="risk_tier")

    op.create_table(
        "raw_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", event_source, nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_event_id", UUID(as_uuid=True), sa.ForeignKey("raw_events.id"), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", incident_status, nullable=False, server_default="open"),
        sa.Column("risk_tier", risk_tier, nullable=True),
        sa.Column("summary_embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_raw_event_id", "incidents", ["raw_event_id"])
    op.create_index("ix_incidents_status", "incidents", ["status"])


def downgrade() -> None:
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_index("ix_incidents_raw_event_id", table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("raw_events")

    sa.Enum(name="risk_tier").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="incident_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="event_source").drop(op.get_bind(), checkfirst=True)
