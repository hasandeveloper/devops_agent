import uuid

from sqlalchemy.orm import Session

from app.models import Incident, RawEvent
from config.vectorstore import get_vectorstore


def persist_incident(db: Session, *, raw_event_id: uuid.UUID, diagnosis: dict) -> Incident:
    incident = Incident(
        raw_event_id=raw_event_id,
        title=diagnosis["title"],
        description=diagnosis["description"],
        risk_tier=diagnosis["risk_tier"],
    )
    db.add(incident)

    raw_event = db.get(RawEvent, raw_event_id)
    raw_event.processed = True

    db.commit()
    db.refresh(incident)

    # RiskTier loads as an enum instance, not a plain string -- unwrap it so the
    # vectorstore's JSONB metadata stores "high", not "RiskTier.high".
    risk_tier = incident.risk_tier.value if hasattr(incident.risk_tier, "value") else incident.risk_tier
    get_vectorstore().add_texts(
        [f"{incident.title}\n{incident.description}"],
        metadatas=[{"title": incident.title, "description": incident.description, "risk_tier": risk_tier}],
        ids=[str(incident.id)],
    )

    return incident
