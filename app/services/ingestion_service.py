from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EventSource, RawEvent


def store_raw_event(
    db: Session,
    *,
    source: EventSource,
    event_type: str,
    resource_id: str | None,
    payload: dict,
    external_event_id: str | None = None,
) -> tuple[RawEvent, bool]:
    """Returns (event, is_new). is_new is False when external_event_id has already
    been seen for this source -- a redelivery of the same SNS/GitHub notification,
    not a new one. The caller should skip whatever happens next (enqueueing a Celery
    task) in that case; see the unique constraint's own comment on RawEvent for why.

    external_event_id being None (a source that doesn't pass one) always inserts a
    new row -- NULLs never conflict with each other in Postgres, so there's nothing
    to detect a duplicate against.
    """
    event = RawEvent(
        source=source, event_type=event_type, resource_id=resource_id, payload=payload, external_event_id=external_event_id
    )
    db.add(event)
    if external_event_id is None:
        db.commit()
        db.refresh(event)
        return event, True

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalars(
            select(RawEvent).where(RawEvent.source == source, RawEvent.external_event_id == external_event_id)
        ).one()
        return existing, False
    else:
        db.refresh(event)
        return event, True
