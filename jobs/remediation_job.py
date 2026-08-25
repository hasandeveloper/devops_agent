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
            outcome = handler["verb"] if result["action_taken"] else f"skipped: {result['skipped']}"
            action = mark_remediation_result(db, remediation_id, status=RemediationStatus.executed, result=outcome)

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

        # Fallback for a large "Approve All Remaining" batch: response_url is only good
        # for ~5 uses/30min (confirmed empirically -- see post_remediation_update's
        # docstring), so with enough candidates approved at once, the *last* completions
        # to finish can't post their update at all and the message goes stale. Once
        # nothing is left pending for this incident, guarantee one accurate final
        # message via the durable channel webhook instead of the exhausted response_url.
        # Only fires on failure -- the normal, small-batch case (response_url succeeds)
        # is unchanged, no extra message. In a very large batch, more than one of the
        # tail-end completions could independently satisfy this and post a duplicate
        # final message -- accepted as a rare, harmless redundancy rather than adding
        # cross-task locking for it.
        terminal_statuses = {RemediationStatus.executed, RemediationStatus.failed, RemediationStatus.rejected}
        all_terminal = all(a["status"] in {s.value for s in terminal_statuses} for a in remediations)
        if not posted and all_terminal:
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
