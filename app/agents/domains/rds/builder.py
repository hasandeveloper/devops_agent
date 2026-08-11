from langgraph.graph import END, StateGraph

from app.agents.domains.rds.nodes.diagnose import diagnose
from app.agents.domains.rds.nodes.gather_context import gather_context
from app.agents.domains.rds.nodes.investigate_further import investigate_further
from app.agents.domains.rds.nodes.notify_slack import notify_slack
from app.agents.domains.rds.nodes.persist_incident import persist_incident_node
from app.agents.domains.rds.nodes.retrieve_similar_incidents import retrieve_similar_incidents
from app.agents.shared.state.agent import AgentState


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("gather_context", gather_context)
    graph.add_node("retrieve_similar_incidents", retrieve_similar_incidents)
    graph.add_node("investigate_further", investigate_further)
    graph.add_node("diagnose", diagnose)
    graph.add_node("persist_incident", persist_incident_node)
    graph.add_node("notify_slack", notify_slack)
    graph.set_entry_point("gather_context")
    graph.add_edge("gather_context", "retrieve_similar_incidents")
    graph.add_edge("retrieve_similar_incidents", "investigate_further")
    graph.add_edge("investigate_further", "diagnose")
    graph.add_edge("diagnose", "persist_incident")
    graph.add_edge("persist_incident", "notify_slack")
    graph.add_edge("notify_slack", END)
    return graph.compile()


_graph = _build_graph()


async def handle(raw_event: dict) -> str:
    result = await _graph.ainvoke(
        {
            "raw_event": raw_event,
            "context": None,
            "similar_incidents": None,
            "investigation": None,
            "diagnosis": None,
            "incident_id": None,
            "slack_message_ts": None,
        }
    )
    return result["incident_id"]
