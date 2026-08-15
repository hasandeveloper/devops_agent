import logging
import uuid

from app.agents.shared.state.agent import AgentState
from app.services.incident_service import is_incident_notified, mark_incident_notified
from app.services.slack_notifier import post_diagnosis
from db import SessionLocal

logger = logging.getLogger(__name__)


async def notify_slack(state: AgentState) -> dict:
    incident_id = uuid.UUID(state["incident_id"])

    # Two short-lived sessions, not one held open across the Slack POST below --
    # there's no reason to keep a DB connection checked out of the pool for the
    # duration of a slow external HTTP call.
    db = SessionLocal()
    try:
        if is_incident_notified(db, incident_id):
            # Only reachable via a Celery retry: a prior attempt already posted this
            # incident to Slack successfully but the pipeline failed after that point.
            logger.info("incident_id=%s already notified, skipping", incident_id)
            return {"slack_message_ts": None}
    finally:
        db.close()

    await post_diagnosis(incident_id=state["incident_id"], diagnosis=state["diagnosis"])

    db = SessionLocal()
    try:
        mark_incident_notified(db, incident_id)
    finally:
        db.close()

    return {"slack_message_ts": None}
