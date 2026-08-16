import json
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from config.mcp import stdio_server
from config.reliability.mcp_timeouts import invoke_tool

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.mcp.rds.mcp_server")}


def _parse_mcp_result(result):
    """MCP tool results arrive as a list of {"type": "text", "text": "..."} blocks --
    one block per returned item. Unwrap to the plain Python value the tool actually returned.

    A dict/list-returning tool's text is JSON-encoded (e.g. '{"status": "ok"}'); a plain
    str-returning tool's text is the bare string itself (e.g. "dev", not '"dev"') -- confirmed
    empirically, FastMCP doesn't JSON-encode primitive string returns. json.loads fails on the
    latter, so fall back to the raw text rather than assuming every tool's output is JSON.
    """

    def _parse_one(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    if isinstance(result, list) and result and all(isinstance(item, dict) and "text" in item for item in result):
        parsed = [_parse_one(item["text"]) for item in result]
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
    alarm_environment_tool = next(t for t in tools if t.name == "get_alarm_environment")
    active_connections_tool = next(t for t in tools if t.name == "get_active_connections")
    lock_waits_tool = next(t for t in tools if t.name == "get_lock_waits")

    dimension_name, dimension_value = next(iter(dims.items()))

    # Alarms dimensioned by instance (not cluster) need one extra lookup to
    # resolve the cluster id -- describe_db_cluster requires the cluster id.
    if "DBClusterIdentifier" in dims:
        cluster_id = dims["DBClusterIdentifier"]
    elif "DBInstanceIdentifier" in dims:
        instance_info = _parse_mcp_result(
            await invoke_tool(describe_instance_tool, {"instance_id": dims["DBInstanceIdentifier"]})
        )
        cluster_id = instance_info["db_cluster_identifier"]
    else:
        cluster_id = state["raw_event"].get("resource_id")

    cluster_info = _parse_mcp_result(await invoke_tool(describe_cluster_tool, {"cluster_id": cluster_id}))
    recent_trend = _parse_mcp_result(
        await invoke_tool(
            trend_tool,
            {
                "namespace": trigger.get("Namespace", "AWS/RDS"),
                "metric_name": trigger.get("MetricName", ""),
                "dimension_name": dimension_name,
                "dimension_value": dimension_value,
                "minutes": 30,
            },
        )
    )

    # The alarm's "environment" tag decides which app database the DB-internal
    # tools below connect to -- it's not in the SNS payload, only fetchable via
    # this separate lookup on the alarm's own ARN.
    environment = _parse_mcp_result(await invoke_tool(alarm_environment_tool, {"alarm_arn": payload["AlarmArn"]}))
    active_connections = _parse_mcp_result(await invoke_tool(active_connections_tool, {"environment": environment}))
    lock_waits = _parse_mcp_result(await invoke_tool(lock_waits_tool, {"environment": environment}))

    logger.info(
        "raw_event_id=%s cluster_id=%s environment=%s lock_waits=%d",
        state["raw_event"]["id"],
        cluster_id,
        environment,
        len(lock_waits),
    )

    return {
        "context": {
            "cluster_info": cluster_info,
            "recent_trend": recent_trend,
            "environment": environment,
            "active_connections": active_connections,
            "lock_waits": lock_waits,
        }
    }
