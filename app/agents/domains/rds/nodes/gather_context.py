import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.shared.state.agent import AgentState
from config.mcp import parse_mcp_list_result, parse_mcp_result, stdio_server
from config.reliability.mcp_timeouts import invoke_tool

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds": stdio_server("app.agents.tools.mcp.rds.mcp_server")}


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
        instance_info = parse_mcp_result(
            await invoke_tool(describe_instance_tool, {"instance_id": dims["DBInstanceIdentifier"]})
        )
        cluster_id = instance_info["db_cluster_identifier"]
    else:
        cluster_id = state["raw_event"].get("resource_id")

    cluster_info = parse_mcp_result(await invoke_tool(describe_cluster_tool, {"cluster_id": cluster_id}))
    # parse_mcp_list_result, not parse_mcp_result -- these three tools return list[dict],
    # and parse_mcp_result alone collapses a single-item list to a bare dict (see its
    # docstring). recent_trend/active_connections/lock_waits are all iterated as lists
    # downstream, and a single datapoint/state-row/lock-pair is a completely normal,
    # not-even-rare result to get back.
    recent_trend = parse_mcp_list_result(
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
    environment = parse_mcp_result(await invoke_tool(alarm_environment_tool, {"alarm_arn": payload["AlarmArn"]}))
    active_connections = parse_mcp_list_result(await invoke_tool(active_connections_tool, {"environment": environment}))
    lock_waits = parse_mcp_list_result(await invoke_tool(lock_waits_tool, {"environment": environment}))

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
