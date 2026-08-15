import logging

from app.agents.shared.state.agent import AgentState
from app.services.incident_service import persist_incident
from db import SessionLocal

logger = logging.getLogger(__name__)


def persist_incident_node(state: AgentState) -> dict:
    db = SessionLocal()
    try:
        incident = persist_incident(db, raw_event_id=state["raw_event"]["id"], diagnosis=state["diagnosis"])
        logger.info("raw_event_id=%s incident_id=%s", state["raw_event"]["id"], incident.id)
        return {"incident_id": str(incident.id)}
    except Exception:
        # Without this, a failure partway through persist_incident() (e.g. the
        # incident insert succeeds but raw_event.processed = True's flush fails)
        # would leave the session's pending changes uncommitted-but-unrolled-back
        # when db.close() runs below -- explicit here rather than relying on the
        # connection pool's own reset-on-return behavior to clean it up.
        db.rollback()
        raise
    finally:
        db.close()
