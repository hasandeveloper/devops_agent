import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Dimension for the embedding model used when incidents get summarized (Phase 3).
EMBEDDING_DIM = 1536


class IncidentStatus(str, enum.Enum):
    open = "open"
    diagnosing = "diagnosing"
    awaiting_approval = "awaiting_approval"
    resolved = "resolved"
    rejected = "rejected"


class RiskTier(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_events.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, name="incident_status"), default=IncidentStatus.open
    )
    risk_tier: Mapped[RiskTier | None] = mapped_column(Enum(RiskTier, name="risk_tier"), nullable=True)
    # Populated once a diagnosis is generated (Phase 3+) for pgvector similarity search.
    summary_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_event: Mapped["RawEvent"] = relationship(back_populates="incidents")
