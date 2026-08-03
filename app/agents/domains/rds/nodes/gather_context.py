import json

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from config.mcp import stdio_server

MCP_SERVERS = {"rds": stdio_server("app.agents.domains.rds.mcp_server")}


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
