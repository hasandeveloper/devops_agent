import asyncio
import logging
import uuid

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.models import Incident, RemediationStatus
from app.services.remediation_service import (
    action_to_dict,
    get_remediation,
    get_remediations_for_incident,
    mark_remediation_result,
    recompute_incident_status,
)
from app.services.slack_service import post_diagnosis, post_remediation_update
from config.celery_app import celery_app
from config.mcp import parse_mcp_result, stdio_server
from config.reliability.mcp_timeouts import invoke_tool
from db import SessionLocal

logger = logging.getLogger(__name__)

MCP_SERVERS = {"rds_remediation": stdio_server("app.agents.tools.mcp.rds.remediation_mcp_server")}

# The one place that needs to know about every remediation action type. Everything
# else (the graph, Slack rendering, the interaction handler) stays generic by row id,
# not by action type -- adding a future phase means one more entry here, not new
# branching logic elsewhere.
_ACTION_HANDLERS = {
    "cancel_query": {
        "tool": "cancel_backend",
        "verb": "cancelled",
        "build_args": lambda a: {
            "environment": a.environment,
            "pid": a.target_pid,
            "expected_query_snippet": a.target_query,
        },
    },
    "terminate_idle_connection": {
        "tool": "terminate_backend",
        "verb": "terminated",
        "build_args": lambda a: {
            "environment": a.environment,
            "pid": a.target_pid,
            "expected_query_snippet": a.target_query,
            "expected_backend_start": a.target_backend_start,
        },
    },
}


def _outcome_for(result: dict, verb: str, tool_name: str) -> tuple[RemediationStatus, str]:
    """Turns a write tool's raw result into (status, result text) -- pulled out as its
    own pure function so this decision is testable without a database or an MCP call.

    Three distinct outcomes, not two:
      - action_taken=False: the pre-check itself declined (pid gone, state changed,
        wrong query). Nothing was attempted -- a safe no-op, not a failure.
      - action_taken=True, signal_sent=True: pg_cancel_backend/pg_terminate_backend
        was called and Postgres confirms the signal was delivered.
      - action_taken=True, signal_sent=False: the pre-check passed and Postgres was
        actually asked to act, but it reports the signal was NOT delivered. Reporting
        this as a plain success would tell a human "cancelled" when nothing may have
        actually happened -- treated as a failure, same as an exception, so it gets
        the same visibly-different Slack copy instead of a false checkmark.
    """
    if not result["action_taken"]:
        return RemediationStatus.executed, f"skipped: {result['skipped']}"
    if result["signal_sent"]:
        return RemediationStatus.executed, verb
    return RemediationStatus.failed, f"{tool_name} returned signal_sent=false -- the write may not have taken effect"


async def _execute(remediation_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        action = get_remediation(db, remediation_id)
        if action is None or action.status != RemediationStatus.approved:
            # Nothing to do -- already executed by a prior attempt, or somehow never
            # reached `approved` (shouldn't happen: only the approve path enqueues this).
            logger.warning("remediation_id=%s not in approved state, skipping execution", remediation_id)
            return

        handler = _ACTION_HANDLERS[action.action_type]
        try:
            client = MultiServerMCPClient(MCP_SERVERS)
            tools = await client.get_tools()
            tool = next(t for t in tools if t.name == handler["tool"])
            result = parse_mcp_result(await invoke_tool(tool, handler["build_args"](action)))
        except Exception as exc:
            logger.exception("remediation_id=%s %s call failed", remediation_id, handler["tool"])
            action = mark_remediation_result(db, remediation_id, status=RemediationStatus.failed, result=str(exc))
        else:
            outcome_status, outcome_result = _outcome_for(result, handler["verb"], handler["tool"])
            if outcome_status == RemediationStatus.failed:
                logger.warning("remediation_id=%s %s", remediation_id, outcome_result)
            action = mark_remediation_result(db, remediation_id, status=outcome_status, result=outcome_result)

        recompute_incident_status(db, action.incident_id)

        incident = db.get(Incident, action.incident_id)
        remediations = [action_to_dict(a) for a in get_remediations_for_incident(db, action.incident_id)]

        posted = False
        if action.slack_response_url:
            posted = await post_remediation_update(
                action.slack_response_url,
                incident_id=str(incident.id),
                title=incident.title,
                risk_tier=incident.risk_tier.value,
                description=incident.description,
                remediations=remediations,
            )

        # "Approve All Remaining" starts one Celery job per pid, and all of those jobs
        # run at the same time. Each job finishes by re-reading every row for this
        # incident and re-posting the whole message to Slack. Because the jobs don't
        # coordinate with each other, two things can go wrong:
        #
        #   1. response_url only works ~5 times / 30 minutes (see post_remediation_update's
        #      docstring). In a big batch, a late job's post can simply fail with a 404.
        #
        #   2. Even when every post succeeds, only the one that *arrives at Slack last*
        #      stays on screen. That's not necessarily the one built from the freshest
        #      data -- a job can finish, but its Slack update loses the race to an older
        #      update from a job that was already slightly ahead of it. The result: Slack
        #      keeps showing a row as "still running" even though it finished moments ago.
        #
        # The fix for both: once every row for this incident is done (approved, rejected,
        # or failed), send one more guaranteed-fresh message through the durable Slack
        # webhook -- not the possibly-expired, possibly-out-of-order response_url.
        #
        # This only matters for multi-row batches. A single-row approval has only one
        # job, so nothing can race it -- its one successful post is already final and
        # correct, and sending a second "final" message for it would just be noise.
        terminal_statuses = {RemediationStatus.executed, RemediationStatus.failed, RemediationStatus.rejected}
        all_terminal = all(a["status"] in {s.value for s in terminal_statuses} for a in remediations)
        is_multi_row_batch = len(remediations) > 1
        if all_terminal and (not posted or is_multi_row_batch):
            await post_diagnosis(
                incident_id=str(incident.id),
                diagnosis={"title": incident.title, "risk_tier": incident.risk_tier.value, "description": incident.description},
                remediation=remediations,
            )
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=0)
def execute_remediation_job(self, remediation_id: str) -> None:
    # No retry: a failed write-tool call is recorded as `failed` on the row itself
    # (visible in the final Slack message) rather than retried -- retrying could mean
    # acting on a pid a second time after conditions have already changed again, which
    # is exactly the TOCTOU risk each write tool's own re-check exists to avoid.
    asyncio.run(_execute(uuid.UUID(remediation_id)))
