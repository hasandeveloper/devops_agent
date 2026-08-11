"""Read-only MCP server for the RDS domain agent. Run as a stdio subprocess --
never exposes a mutating boto3 call. See documentation/devops/monitoring-setup.md
for the alarms this agent diagnoses.
"""

from datetime import datetime, timedelta, timezone

import boto3
import psycopg
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


@mcp.tool()
def get_alarm_environment(alarm_arn: str) -> str:
    """Read-only: the "environment" tag value (dev/stag/production) on a CloudWatch alarm.

    Alarm tags aren't included in the SNS notification payload -- this is a separate lookup.
    Determines which app database the other DB diagnostic tools should connect to."""
    client = boto3.client("cloudwatch", region_name=settings.aws_region)
    resp = client.list_tags_for_resource(ResourceARN=alarm_arn)
    for tag in resp["Tags"]:
        if tag["Key"] == "environment":
            return tag["Value"]
    raise ValueError(f"alarm {alarm_arn!r} has no 'environment' tag")


def _connect_app_db(environment: str) -> psycopg.Connection:
    config = settings.app_db_config(environment)
    return psycopg.connect(
        host=config.host,
        port=config.port,
        user=config.readonly_username,
        password=config.readonly_password,
        dbname=config.database,
    )


@mcp.tool()
def get_active_connections(environment: str) -> list[dict]:
    """Read-only: connection counts by state on the app database, e.g. active vs idle vs idle-in-transaction."""
    with _connect_app_db(environment) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT state, count(*) AS count
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY state
        """)
        return [{"state": row[0], "count": row[1]} for row in cur.fetchall()]


@mcp.tool()
def get_lock_waits(environment: str) -> list[dict]:
    """Read-only: currently blocked queries and what's blocking them, if any."""
    with _connect_app_db(environment) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT blocked_locks.pid AS blocked_pid,
                   left(blocked_activity.query, 200) AS blocked_query,
                   blocking_locks.pid AS blocking_pid,
                   left(blocking_activity.query, 200) AS blocking_query
            FROM pg_catalog.pg_locks blocked_locks
            JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
            JOIN pg_catalog.pg_locks blocking_locks
                ON blocking_locks.locktype = blocked_locks.locktype
               AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
               AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
               AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
               AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
               AND blocking_locks.pid != blocked_locks.pid
            JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
            WHERE NOT blocked_locks.granted
        """)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


@mcp.tool()
def get_performance_insights_top_sql(instance_id: str, minutes: int = 60) -> list[dict]:
    """Read-only: top SQL by DB load on this instance over the recent window (AWS Performance
    Insights). Statement text is tokenized -- parameter values are stripped, not the literal
    ones passed at runtime -- safe to include verbatim in a diagnosis."""
    rds_client = boto3.client("rds", region_name=settings.aws_region)
    dbi_resource_id = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)["DBInstances"][0][
        "DbiResourceId"
    ]

    pi_client = boto3.client("pi", region_name=settings.aws_region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    resp = pi_client.describe_dimension_keys(
        ServiceType="RDS",
        Identifier=dbi_resource_id,
        StartTime=start,
        EndTime=end,
        Metric="db.load.avg",
        GroupBy={"Group": "db.sql_tokenized", "Limit": 10},
    )
    return [
        {"load": k.get("Total"), "statement": k.get("Dimensions", {}).get("db.sql_tokenized.statement", "")}
        for k in resp.get("Keys", [])
    ]


@mcp.tool()
def explain_query_for_pid(environment: str, pid: int) -> dict:
    """Read-only: query plan (EXPLAIN, never ANALYZE -- doesn't execute anything) for whatever
    query backend `pid` is currently running. Takes only a PID (from get_lock_waits/
    get_active_connections output) -- the query text itself is looked up from pg_stat_activity
    inside this tool, never supplied by the caller, so no free-form SQL ever reaches this call."""
    with _connect_app_db(environment) as conn, conn.cursor() as cur:
        cur.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", (pid,))
        row = cur.fetchone()
        if row is None:
            return {"error": f"no active backend with pid={pid}"}

        query = row[0].strip()

        # psycopg's execute() uses the simple query protocol for parameter-less calls, which
        # silently runs semicolon-separated statements as a batch -- confirmed empirically that
        # "EXPLAIN (FORMAT JSON) SELECT 1; CREATE TABLE x (...)" really creates the table, since
        # EXPLAIN only wraps the first statement. Reject anything but one SELECT/WITH statement
        # before the retrieved text ever reaches execute(), rather than relying solely on the
        # read-only role to block whatever a stacked statement might attempt.
        if query.rstrip(";").count(";") > 0:
            return {"pid": pid, "error": "refusing to explain a multi-statement query"}
        if not query.upper().startswith(("SELECT", "WITH")):
            return {"pid": pid, "error": "refusing to explain a non-SELECT statement"}

        try:
            cur.execute(f"EXPLAIN (FORMAT JSON) {query}")
            return {"pid": pid, "query": query[:200], "plan": cur.fetchone()[0]}
        except psycopg.errors.Error as exc:
            # e.g. the app used server-side prepared statements and pg_stat_activity only has
            # unresolved $1/$2 placeholders left -- EXPLAIN can't run on that, report it rather
            # than crashing the investigation.
            return {"pid": pid, "error": str(exc)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
