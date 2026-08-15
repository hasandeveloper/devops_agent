import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident, RawEvent
from config.vectorstore import get_vectorstore


def _embed_incident(incident: Incident) -> None:
    """(Re-)write this incident's vectorstore entry to match its current fields.

    Safe to call repeatedly for the same incident: PGVector's add_texts() upserts by
    id (ON CONFLICT DO UPDATE), not insert-or-fail.
    """
    # RiskTier loads as an enum instance, not a plain string -- unwrap it so the
    # vectorstore's JSONB metadata stores "high", not "RiskTier.high".
    risk_tier = incident.risk_tier.value if hasattr(incident.risk_tier, "value") else incident.risk_tier
    get_vectorstore().add_texts(
        [f"{incident.title}\n{incident.description}"],
        metadatas=[{"title": incident.title, "description": incident.description, "risk_tier": risk_tier}],
        ids=[str(incident.id)],
    )


def persist_incident(db: Session, *, raw_event_id: uuid.UUID, diagnosis: dict) -> Incident:
    # A Celery retry re-runs the *entire* diagnosis pipeline from scratch (see
    # jobs/webhooks_job.py's self.retry()) -- including this function. If an earlier
    # attempt already made it this far and committed successfully, but the pipeline
    # failed later (e.g. the Slack post in notify_slack.py), retrying would otherwise
    # create a second incident row and a second vectorstore embedding for the exact
    # same alarm. One raw_event only ever produces one incident, so reuse it instead
    # of creating a duplicate.
    #
    # The Slack-notification side of this same retry problem is handled separately --
    # see is_incident_notified()/mark_incident_notified() below and notify_slack.py.
    incident = db.execute(select(Incident).filter_by(raw_event_id=raw_event_id)).scalar_one_or_none()

    if incident is None:
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

    # Deliberately NOT skipped when the incident already existed above -- see
    # _embed_incident()'s docstring for why re-calling it is always safe. That matters
    # for the retry case where a prior attempt committed the incident row but crashed
    # before reaching this line -- without re-attempting the embedding here, that
    # incident would stay permanently invisible to retrieve_similar_incidents.py.
    _embed_incident(incident)

    return incident


def is_incident_notified(db: Session, incident_id: uuid.UUID) -> bool:
    """Has a Slack notification already gone out for this incident?

    Checked by notify_slack.py *before* posting, so a Celery retry that re-reaches
    notify_slack after a prior attempt already posted successfully doesn't post again.
    """
    incident = db.get(Incident, incident_id)
    return incident.notified_at is not None


def mark_incident_notified(db: Session, incident_id: uuid.UUID) -> None:
    """Record that a Slack notification was just sent for this incident.

    Called by notify_slack.py *after* the Slack post succeeds, not before -- marking
    this first and sending second would mean a failed send gets permanently treated as
    "already notified" by is_incident_notified() and never retried, which silently
    drops the notification instead of just risking a duplicate.
    """
    incident = db.get(Incident, incident_id)
    incident.notified_at = datetime.now(timezone.utc)
    db.commit()
