from app.agents.shared.state.agent import AgentState
from app.services.incident_service import persist_incident
from db import SessionLocal


def persist_incident_node(state: AgentState) -> dict:
    db = SessionLocal()
    try:
        incident = persist_incident(db, raw_event_id=state["raw_event"]["id"], diagnosis=state["diagnosis"])
        return {"incident_id": str(incident.id)}
    finally:
        db.close()
