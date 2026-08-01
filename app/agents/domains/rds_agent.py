import json

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, StateGraph

from app.agents.shared.diagnosis_schema import Diagnosis
from app.agents.shared.llm import get_llm
from app.agents.shared.mcp_config import stdio_server
from app.agents.shared.state import AgentState
from app.services.incident_service import persist_incident
from app.services.slack_notifier import post_diagnosis
from db import SessionLocal

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.rds_mcp_server")}


def _parse_mcp_result(result):
    """MCP tool results arrive as a list of {"type": "text", "text": "<json>"} blocks --
    one block per returned item. Unwrap to the plain Python value the tool actually returned."""
    if isinstance(result, list) and result and all(isinstance(item, dict) and "text" in item for item in result):
        parsed = [json.loads(item["text"]) for item in result]
        return parsed[0] if len(parsed) == 1 else parsed
    return result


async def gather_context(state: AgentState) -> dict:
    payload = state["raw_event"]["payload"]
    trigger = payload.get("Trigger", {})
    dims = {d["name"]: d["value"] for d in trigger.get("Dimensions", [])}

    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()
    describe_cluster_tool = next(t for t in tools if t.name == "describe_db_cluster")
    describe_instance_tool = next(t for t in tools if t.name == "describe_db_instance")
    trend_tool = next(t for t in tools if t.name == "get_recent_metric_trend")

    dimension_name, dimension_value = next(iter(dims.items()))

    # Alarms dimensioned by instance (not cluster) need one extra lookup to
    # resolve the cluster id -- describe_db_cluster requires the cluster id.
    if "DBClusterIdentifier" in dims:
        cluster_id = dims["DBClusterIdentifier"]
    elif "DBInstanceIdentifier" in dims:
        instance_info = _parse_mcp_result(
            await describe_instance_tool.ainvoke({"instance_id": dims["DBInstanceIdentifier"]})
        )
        cluster_id = instance_info["db_cluster_identifier"]
    else:
        cluster_id = state["raw_event"].get("resource_id")

    cluster_info = _parse_mcp_result(await describe_cluster_tool.ainvoke({"cluster_id": cluster_id}))
    recent_trend = _parse_mcp_result(
        await trend_tool.ainvoke(
            {
                "namespace": trigger.get("Namespace", "AWS/RDS"),
                "metric_name": trigger.get("MetricName", ""),
                "dimension_name": dimension_name,
                "dimension_value": dimension_value,
                "minutes": 30,
            }
        )
    )
    return {"context": {"cluster_info": cluster_info, "recent_trend": recent_trend}}


async def diagnose(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(Diagnosis)
    prompt = (
        "You are diagnosing an AWS RDS/Aurora CloudWatch alarm for a DevOps monitoring system.\n\n"
        f"Alarm payload:\n{json.dumps(state['raw_event']['payload'], indent=2, default=str)}\n\n"
        f"Additional context gathered (cluster config + recent metric trend):\n"
        f"{json.dumps(state['context'], indent=2, default=str)}\n\n"
        "Produce a diagnosis: a short title, a description of what's happening and the likely "
        "cause, and a risk tier (low/medium/high)."
    )
    diagnosis: Diagnosis = await llm.ainvoke(prompt)
    return {"diagnosis": diagnosis.model_dump(mode="json")}


def persist_incident_node(state: AgentState) -> dict:
    db = SessionLocal()
    try:
        incident = persist_incident(db, raw_event_id=state["raw_event"]["id"], diagnosis=state["diagnosis"])
        return {"incident_id": str(incident.id)}
    finally:
        db.close()


async def notify_slack(state: AgentState) -> dict:
    await post_diagnosis(incident_id=state["incident_id"], diagnosis=state["diagnosis"])
    return {"slack_message_ts": None}


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
