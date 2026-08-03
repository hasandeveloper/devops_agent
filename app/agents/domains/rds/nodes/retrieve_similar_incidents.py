from app.agents.shared.state.agent import AgentState
from app.services.embedding_service import embed_text
from app.services.incident_service import find_similar_incidents
from db import SessionLocal


def _build_query_text(payload: dict) -> str:
    trigger = payload.get("Trigger", {})
    dims = ", ".join(f"{d.get('name')}={d.get('value')}" for d in trigger.get("Dimensions", []))
    return (
        f"{payload.get('AlarmName', '')} {trigger.get('Namespace', '')} "
        f"{trigger.get('MetricName', '')} {dims} {payload.get('NewStateReason', '')}"
    )


def retrieve_similar_incidents(state: AgentState) -> dict:
    query_text = _build_query_text(state["raw_event"]["payload"])
    embedding = embed_text(query_text)
    db = SessionLocal()
    try:
        similar = find_similar_incidents(db, embedding, limit=3)
        return {
            "similar_incidents": [
                {"title": i.title, "description": i.description, "risk_tier": i.risk_tier} for i in similar
            ]
        }
    finally:
        db.close()
