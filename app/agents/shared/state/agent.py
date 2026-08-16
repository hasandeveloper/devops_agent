from typing import Optional, TypedDict


class AgentState(TypedDict):
    """Shared LangGraph state shape -- same across all 4 domain agents.

    Each node reads/writes a slice of this as the graph progresses:
    raw_event (input) -> context (gather_context) -> similar_incidents
    (retrieve_similar_incidents) -> investigation + query_evidence
    (investigate_further) -> diagnosis (diagnose) -> incident_id
    (persist_incident) -> slack_message_ts (notify_slack).
    """

    raw_event: dict
    context: Optional[dict]
    similar_incidents: Optional[list[dict]]
    investigation: Optional[str]
    # The exact SQL text behind investigation's prose, straight from the MCP tool
    # results -- not paraphrased by an LLM. See investigate_further.py's
    # _extract_query_evidence(). Posted to Slack, not persisted on the incident.
    query_evidence: Optional[list[dict]]
    diagnosis: Optional[dict]
    incident_id: Optional[str]
    slack_message_ts: Optional[str]
