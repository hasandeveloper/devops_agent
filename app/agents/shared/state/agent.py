from typing import Optional, TypedDict


class AgentState(TypedDict):
    """Shared LangGraph state shape -- same across all 4 domain agents.

    Each node reads/writes a slice of this as the graph progresses:
    raw_event (input) -> context (gather_context) -> similar_incidents
    (retrieve_similar_incidents) -> investigation (investigate_further) ->
    diagnosis (diagnose) -> incident_id (persist_incident) -> slack_message_ts
    (notify_slack).
    """

    raw_event: dict
    context: Optional[dict]
    similar_incidents: Optional[list[dict]]
    investigation: Optional[str]
    diagnosis: Optional[dict]
    incident_id: Optional[str]
    slack_message_ts: Optional[str]
