"""Read-only MCP server for the RDS domain agent. Run as a stdio subprocess --
never exposes a mutating boto3 call. See documentation/devops/monitoring-setup.md
for the alarms this agent diagnoses.
"""

from datetime import datetime, timedelta, timezone

import boto3
from mcp.server.fastmcp import FastMCP

from config import settings

mcp = FastMCP("rds-mcp-server")


@mcp.tool()
def describe_db_cluster(cluster_id: str) -> dict:
    """Read-only: current config/status of an Aurora DB cluster -- scaling range, engine, writer/reader members."""
    client = boto3.client("rds", region_name=settings.aws_region)
    resp = client.describe_db_clusters(DBClusterIdentifier=cluster_id)
    cluster = resp["DBClusters"][0]
    return {
        "status": cluster["Status"],
        "engine": cluster["Engine"],
        "engine_version": cluster["EngineVersion"],
        "serverless_v2_scaling": cluster.get("ServerlessV2ScalingConfiguration"),
        "members": [
            {"instance": m["DBInstanceIdentifier"], "is_writer": m["IsClusterWriter"]}
            for m in cluster["DBClusterMembers"]
        ],
    }


@mcp.tool()
def describe_db_instance(instance_id: str) -> dict:
    """Read-only: resolve a DB instance to its parent cluster identifier and status.

    An Aurora alarm dimensioned by DBInstanceIdentifier (not DBClusterIdentifier)
    needs this first -- describe_db_cluster requires the cluster id, not the instance id."""
    client = boto3.client("rds", region_name=settings.aws_region)
    resp = client.describe_db_instances(DBInstanceIdentifier=instance_id)
    instance = resp["DBInstances"][0]
    return {
        "instance_status": instance["DBInstanceStatus"],
        "db_cluster_identifier": instance.get("DBClusterIdentifier"),
        "instance_class": instance.get("DBInstanceClass"),
    }


@mcp.tool()
def get_recent_metric_trend(
    namespace: str,
    metric_name: str,
    dimension_name: str,
    dimension_value: str,
    minutes: int = 30,
) -> list[dict]:
    """Read-only: recent datapoints for a CloudWatch metric, so the agent sees the trend, not just the single threshold breach."""
    client = boto3.client("cloudwatch", region_name=settings.aws_region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": dimension_name, "Value": dimension_value}],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Average", "Maximum"],
    )
    datapoints = sorted(resp["Datapoints"], key=lambda d: d["Timestamp"])
    return [
        {"timestamp": dp["Timestamp"].isoformat(), "average": dp["Average"], "maximum": dp["Maximum"]}
        for dp in datapoints
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
