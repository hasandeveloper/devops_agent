"""raw_events: add external_event_id, deduplicate redelivered SNS/GitHub events

Stores the sender's own "this exact delivery" id -- SNS's MessageId for a cloudwatch
event, GitHub's X-GitHub-Delivery header for a github_actions event. Unique alongside
source so a redelivery of the same notification (SNS is documented as at-least-once
delivery) is recognized and skipped instead of producing a duplicate incident, a
duplicate Slack post, and (on a medium/high-risk alarm) a duplicate remediation
proposal. NULLs never conflict with each other in Postgres, so this is only enforced
when a row actually sets the value -- see app/models/events.py's own comment.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("raw_events", sa.Column("external_event_id", sa.String(length=255), nullable=True))
    op.create_unique_constraint(
        "uq_raw_events_source_external_event_id", "raw_events", ["source", "external_event_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_raw_events_source_external_event_id", "raw_events", type_="unique")
    op.drop_column("raw_events", "external_event_id")
