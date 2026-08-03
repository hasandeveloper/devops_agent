import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident, RawEvent
from app.services.embedding_service import embed_text


def persist_incident(db: Session, *, raw_event_id: uuid.UUID, diagnosis: dict) -> Incident:
    incident = Incident(
        raw_event_id=raw_event_id,
        title=diagnosis["title"],
        description=diagnosis["description"],
        risk_tier=diagnosis["risk_tier"],
        summary_embedding=embed_text(f"{diagnosis['title']}\n{diagnosis['description']}"),
    )
    db.add(incident)

    raw_event = db.get(RawEvent, raw_event_id)
    raw_event.processed = True

    db.commit()
    db.refresh(incident)
    return incident


def find_similar_incidents(db: Session, query_embedding: list[float], limit: int = 3) -> list[Incident]:
    # TODO(end of phase 3): plain top-k cosine similarity can return near-duplicate
    # incidents (e.g. 3 prior alerts for the same recurring issue). Switch to MMR
    # (Maximal Marginal Relevance) so results stay relevant but diverse.
    stmt = (
        select(Incident)
        .where(Incident.summary_embedding.is_not(None))
        .order_by(Incident.summary_embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    return list(db.scalars(stmt))
