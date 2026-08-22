import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RemediationStatus(str, enum.Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"
    failed = "failed"


class RemediationAction(Base):
    """One proposed remediation action against a single target (e.g. one PID to
    cancel) -- an incident with 3 flagged queries gets 3 rows, each independently
    approvable. See propose_remediation.py (creation) and jobs/remediation_job.py
    (execution).
    """

    __tablename__ = "remediation_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    # Plain string, not a DB enum -- later phases (terminate_connection, vacuum_table, ...)
    # add new action types without an ALTER TYPE migration each time.
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    target_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Snapshot at proposal time -- used both for the Slack message and as the
    # expected_query_snippet re-check at execution time (see cancel_backend).
    target_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How long the query had been running when proposed, in seconds -- a snapshot for
    # display, not re-queried live, so the Slack message stays accurate when re-rendered
    # after a decision (see slack_service.py's _build_remediation_blocks).
    target_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RemediationStatus] = mapped_column(
        Enum(RemediationStatus, name="remediation_status"), nullable=False, default=RemediationStatus.proposed
    )
    # Slack's response_url from the original button-click payload -- short-lived
    # (~30 min / a handful of uses), used to post the execution outcome back onto
    # the same message. See app/services/slack_service.py.
    slack_response_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    incident: Mapped["Incident"] = relationship(back_populates="remediation_actions")
