import uuid

from sqlalchemy.orm import Session

from app.models import Incident, RawEvent


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
    return incident
