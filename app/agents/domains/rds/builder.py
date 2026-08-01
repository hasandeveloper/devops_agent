from langgraph.graph import END, StateGraph

from app.agents.domains.rds.nodes.diagnose import diagnose
from app.agents.domains.rds.nodes.gather_context import gather_context
from app.agents.domains.rds.nodes.notify_slack import notify_slack
from app.agents.domains.rds.nodes.persist_incident import persist_incident_node
from app.agents.shared.state.agent import AgentState


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("gather_context", gather_context)
    graph.add_node("diagnose", diagnose)
    graph.add_node("persist_incident", persist_incident_node)
    graph.add_node("notify_slack", notify_slack)
    graph.set_entry_point("gather_context")
    graph.add_edge("gather_context", "diagnose")
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
            "diagnosis": None,
            "incident_id": None,
            "slack_message_ts": None,
        }
    )
    return result["incident_id"]
