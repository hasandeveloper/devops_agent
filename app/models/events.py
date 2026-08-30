import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class EventSource(str, enum.Enum):
    cloudwatch = "cloudwatch"
    github_actions = "github_actions"


class RawEvent(Base):
    __tablename__ = "raw_events"
    # A sender can redeliver the exact same notification -- SNS is documented as
    # at-least-once delivery, and will redeliver an already-published message if it
    # doesn't get a fast/successful response the first time. Without this, a redelivery
    # looks like a brand new event and produces a duplicate incident, a duplicate Slack
    # post, and (on a medium/high-risk alarm) a duplicate remediation proposal for
    # something already handled. NULLs never conflict with each other in Postgres, so
    # a source that never sets external_event_id just never gets this protection --
    # not a hard requirement on every row, only enforced when the value is actually set.
    __table_args__ = (UniqueConstraint("source", "external_event_id", name="uq_raw_events_source_external_event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[EventSource] = mapped_column(Enum(EventSource, name="event_source"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # SNS's MessageId for a cloudwatch event, GitHub's X-GitHub-Delivery header for a
    # github_actions event -- whatever the sender uses to mean "this exact delivery."
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False)

    incidents: Mapped[list["Incident"]] = relationship(back_populates="raw_event")
