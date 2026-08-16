from sqlalchemy.orm import Session

from app.models import EventSource, RawEvent


def store_raw_event(
    db: Session,
    *,
    source: EventSource,
    event_type: str,
    resource_id: str | None,
    payload: dict,
) -> RawEvent:
    event = RawEvent(source=source, event_type=event_type, resource_id=resource_id, payload=payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
