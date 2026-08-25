"""Write-capable MCP server for the RDS domain agent's HITL remediation actions.

Deliberately a *separate* process from mcp_server.py (the investigation-time,
read-only server) rather than one more tool added there -- mcp_server.py's own
docstring says it "never exposes a mutating boto3 call", and every tool in this
file exists specifically to make one. Only ever invoked from
jobs/remediation_job.py, after a human has approved the action in Slack -- never
from investigate_further.py's ReAct loop or gather_context.py.
"""

import psycopg
from mcp.server.fastmcp import FastMCP

from config import settings

mcp = FastMCP("rds-remediation-mcp-server")


def _connect_remediation_db(environment: str) -> psycopg.Connection:
    config = settings.remediation_db_config(environment)
    return psycopg.connect(host=config.host, port=config.port, user=config.username, password=config.password, dbname=config.database)


def _query_still_matches(current_query: str | None, expected_query_snippet: str) -> bool:
    """True if `current_query` (freshly read from pg_stat_activity) is still the same
    statement instance `expected_query_snippet` was captured from at proposal time.

    A pure function (no DB access) so this TOCTOU guard has direct unit test coverage,
    same reasoning as mcp_server.py's _explain_safety_violation. Both sides are
    truncated to the same 200 chars get_long_running_queries itself uses (left(query,
    200)), so this is a straightforward comparison, not fuzzy matching -- a pid that's
    moved on to a genuinely different query will essentially never coincidentally match.
    """
    if current_query is None:
        return False
    return current_query[:200] == expected_query_snippet[:200]


def _backend_start_still_matches(current_backend_start: float | None, expected_backend_start: float) -> bool:
    """True if `current_backend_start` (freshly read from pg_stat_activity, as epoch
    seconds) is still the same connection instance `expected_backend_start` was captured
    from at proposal time.

    A connection's start time never changes for its lifetime, so this is a firmer
    identity check than query text alone -- it can't be fooled by a reused pid that
    happens to be running similar-looking text. A tiny tolerance absorbs float
    round-trip imprecision from the epoch-seconds representation (see
    get_idle_in_transaction_connections), not genuine differences.
    """
    if current_backend_start is None:
        return False
    return abs(current_backend_start - expected_backend_start) < 0.001


@mcp.tool()
def cancel_backend(environment: str, pid: int, expected_query_snippet: str) -> dict:
    """Write: cancels the in-flight query on `pid` via pg_cancel_backend -- interrupts
    that query, the session itself stays alive (unlike pg_terminate_backend).

    Re-checks pg_stat_activity for `pid` immediately before acting: if the pid is gone,
    no longer active, or now running a different query than expected_query_snippet
    (the pid was reused by an unrelated newer backend), this skips the cancel entirely
    rather than risk hitting the wrong session -- the human approved cancelling *that*
    query, not whatever now happens to hold the same pid.
    """
    with _connect_remediation_db(environment) as conn, conn.cursor() as cur:
        cur.execute("SELECT state, query FROM pg_stat_activity WHERE pid = %s", (pid,))
        row = cur.fetchone()

        if row is None:
            return {"pid": pid, "action_taken": False, "skipped": "pid no longer present -- query likely finished"}

        state, current_query = row
        if state != "active":
            return {"pid": pid, "action_taken": False, "skipped": f"pid is no longer active (state={state!r})"}
        if not _query_still_matches(current_query, expected_query_snippet):
            return {"pid": pid, "action_taken": False, "skipped": "pid now running a different query -- pid was reused"}

        cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
        signal_sent = cur.fetchone()[0]
        return {"pid": pid, "action_taken": True, "signal_sent": bool(signal_sent)}


@mcp.tool()
def terminate_backend(environment: str, pid: int, expected_query_snippet: str, expected_backend_start: float) -> dict:
    """Write: terminates the connection at `pid` via pg_terminate_backend -- drops the
    whole session, not just its current query (unlike cancel_backend). Used for
    connections that are idle-in-transaction, where there's no running query to cancel.

    Re-checks pg_stat_activity for `pid` immediately before acting: pid still present,
    still idle-in-transaction, still holding the query text it had at proposal time, AND
    still the exact same connection instance (matching backend_start). That last check is
    stronger than cancel_backend's, since a wrong guess here drops a whole connection,
    not just one query.
    """
    with _connect_remediation_db(environment) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT state, query, extract(epoch FROM backend_start)::float8 FROM pg_stat_activity WHERE pid = %s",
            (pid,),
        )
        row = cur.fetchone()

        if row is None:
            return {"pid": pid, "action_taken": False, "skipped": "pid no longer present -- connection already closed"}

        state, current_query, current_backend_start = row
        if state != "idle in transaction":
            return {"pid": pid, "action_taken": False, "skipped": f"pid is no longer idle-in-transaction (state={state!r})"}
        if not _query_still_matches(current_query, expected_query_snippet):
            return {"pid": pid, "action_taken": False, "skipped": "pid now running a different query -- pid was reused"}
        if not _backend_start_still_matches(current_backend_start, expected_backend_start):
            return {
                "pid": pid,
                "action_taken": False,
                "skipped": "pid's connection start time no longer matches -- pid was reused",
            }

        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        signal_sent = cur.fetchone()[0]
        return {"pid": pid, "action_taken": True, "signal_sent": bool(signal_sent)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
