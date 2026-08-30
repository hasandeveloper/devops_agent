import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


_QUERY_EVIDENCE_LABELS = {
    "get_performance_insights_top_sql": "Top-load query",
    "explain_query_for_pid": "Query being explained",
}


def _format_query_evidence(query_evidence: list[dict] | None) -> str:
    """The exact SQL text investigate_further's tools found, straight from AWS/the
    database -- not the LLM's paraphrase of it. Without this, "which query is actually
    causing this" means re-querying Performance Insights by hand after the fact.
    """
    if not query_evidence:
        return ""
    blocks = []
    for item in query_evidence:
        label = _QUERY_EVIDENCE_LABELS.get(item["tool"], item["tool"])
        blocks.append(f"{label}:\n```{item['query']}```")
    return "\n\n" + "\n\n".join(blocks)


def _format_duration(seconds: float) -> str:
    # float() first, not just int() -- tolerates the numeric-as-string shape a Decimal
    # takes after an MCP round trip (see mcp_server.py's ::float8 cast), not just a
    # plain float/int. int("1259.37") raises ValueError; int(float("1259.37")) doesn't.
    total_seconds = int(float(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _format_diagnosis_text(
    *, incident_id: str, title: str, risk_tier: str, description: str, query_evidence: list[dict] | None = None
) -> str:
    return (
        f"*{title}*\n"
        f"Risk: {risk_tier}\n"
        f"{description}"
        f"{_format_query_evidence(query_evidence)}\n"
        f"Incident: {incident_id}"
    )


# Per-action-type copy -- the one place Slack rendering needs to know about each
# remediation type. Everything else in this file stays generic by row shape.
# terminate_idle_connection reads more explicitly than cancel_query on purpose: dropping
# a whole connection is a heavier consequence than interrupting one query, and the
# copy shouldn't pretend otherwise just to reuse cancel_query's wording.
_ACTION_COPY = {
    "cancel_query": {
        "approve_label": "Approve",
        "duration_label": "Duration",
        "verb_done": "cancelled",
        "verb_failed": "cancellation failed",
        "consequence": None,
    },
    "terminate_idle_connection": {
        "approve_label": "Terminate Connection",
        "duration_label": "Idle for",
        "verb_done": "terminated",
        "verb_failed": "termination failed",
        "consequence": "This will drop the connection entirely — the client will need to reconnect.",
    },
}


def _action_copy(action: dict) -> dict:
    return _ACTION_COPY.get(action.get("action_type"), _ACTION_COPY["cancel_query"])


def _remediation_status_line(action: dict) -> str:
    """Static (button-free) text for a remediation row that's already been decided --
    used once a row leaves `proposed` so a re-rendered message shows history instead
    of re-offering buttons for something already acted on.
    """
    pid = action["pid"]
    decided_by = action.get("decided_by") or "someone"
    status = action["status"]
    result = action.get("result") or ""
    copy = _action_copy(action)

    if status == "rejected":
        return f"❌ *PID {pid}* — rejected by {decided_by}"
    if status == "approved":
        return f"⏳ *PID {pid}* — approved by {decided_by}, executing…"
    if status == "failed":
        return f"⚠️ *PID {pid}* — {copy['verb_failed']}: {result} (approved by {decided_by})"
    if status == "executed" and result.startswith("skipped"):
        return f"ℹ️ *PID {pid}* — {result} (approved by {decided_by})"
    return f"✅ *PID {pid}* — {copy['verb_done']} (approved by {decided_by})"


def _build_remediation_row_blocks(action: dict) -> list[dict]:
    if action["status"] != "proposed":
        return [{"type": "section", "text": {"type": "mrkdwn", "text": _remediation_status_line(action)}}]

    copy = _action_copy(action)
    consequence = f"\n\n_{copy['consequence']}_" if copy["consequence"] else ""
    text = (
        f"*PID:* {action['pid']}\n"
        f"*{copy['duration_label']}:* {_format_duration(action['duration_seconds'])}\n"
        f"*Query:* ```{action['query']}```\n\n"
        f"*Reason:*\n{action['rationale']}"
        f"{consequence}"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "block_id": f"remediation_{action['id']}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": copy["approve_label"]},
                    "style": "primary",
                    "action_id": "approve_remediation",
                    "value": action["id"],
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_remediation",
                    "value": action["id"],
                },
            ],
        },
    ]


def _build_remediation_blocks(incident_id: str, remediations: list[dict]) -> list[dict]:
    """Renders every remediation row for an incident -- pending rows with live
    Approve/Reject buttons, already-decided rows as static status text -- plus a
    trailing "Approve All Remaining" button while anything is still pending.

    Called both for the first post (all rows `proposed`) and to rebuild the full
    message after a click (mixed statuses) -- see app/controllers/webhooks.py's
    handle_slack_interaction and jobs/remediation_job.py, which both replace the
    entire message via response_url rather than patching one row.
    """
    if not remediations:
        return []

    blocks: list[dict] = [{"type": "divider"}]
    for action in remediations:
        blocks.extend(_build_remediation_row_blocks(action))
        blocks.append({"type": "divider"})

    if any(a["status"] == "proposed" for a in remediations):
        blocks.append(
            {
                "type": "actions",
                "block_id": "approve_all",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve All Remaining"},
                        "action_id": "approve_all_remediations",
                        "value": incident_id,
                    }
                ],
            }
        )

    return blocks


def _build_message(
    *,
    incident_id: str,
    title: str,
    risk_tier: str,
    description: str,
    query_evidence: list[dict] | None = None,
    remediations: list[dict] | None = None,
) -> dict:
    text = _format_diagnosis_text(
        incident_id=incident_id, title=title, risk_tier=risk_tier, description=description, query_evidence=query_evidence
    )
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    blocks.extend(_build_remediation_blocks(incident_id, remediations or []))
    return {"text": text, "blocks": blocks}


async def post_unauthorized_approver_notice(response_url: str, *, decided_by: str) -> None:
    """Tells the clicking user their approval didn't count, without touching the
    original message -- `replace_original: False` means this is visible only to them,
    not a change everyone in the channel sees, unlike post_remediation_update's full
    message replacement. Best-effort: a failure here is logged, not raised, since the
    caller's own 403 to Slack already communicates the rejection at the protocol level."""
    payload = {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": "You're not authorized to approve remediation actions. Ask an admin to add you to SLACK_APPROVER_ALLOWLIST.",
    }
    try:
        await _post(response_url, payload, log_context=f"unauthorized approver notice for decided_by={decided_by}")
    except httpx.HTTPError:
        logger.exception("failed to post unauthorized approver notice for decided_by=%s", decided_by)


async def _post(url: str, payload: dict, *, log_context: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    logger.info("posted to Slack: %s", log_context)


async def post_diagnosis(
    *, incident_id: str, diagnosis: dict, query_evidence: list[dict] | None = None, remediation: list[dict] | None = None
) -> None:
    if not settings.slack_webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set, skipping notification for incident_id=%s", incident_id)
        return

    payload = _build_message(
        incident_id=incident_id,
        title=diagnosis["title"],
        risk_tier=diagnosis["risk_tier"],
        description=diagnosis["description"],
        query_evidence=query_evidence,
        remediations=remediation,
    )
    await _post(settings.slack_webhook_url, payload, log_context=f"diagnosis for incident_id={incident_id}")


async def post_remediation_update(
    response_url: str, *, incident_id: str, title: str, risk_tier: str, description: str, remediations: list[dict]
) -> bool:
    """Rebuilds and replaces the diagnosis message after a button click or an execution
    job finishes -- response_url replaces the *entire* original message, so this always
    reconstructs the full block set from every row's current status, not just the one
    that changed. Query evidence isn't included here (it's never persisted -- see
    AgentState.query_evidence's docstring) so it disappears from the message after the
    first update; that's an accepted display tradeoff, not a bug.

    response_url is short-lived (~30 min / a handful of uses) -- confirmed empirically
    that Slack returns a plain 404 once exhausted, not a graceful no-op. A failure here
    is logged, not raised, so it never blocks the caller from recording the actual DB
    decision or execution result, which remains the source of truth -- but the caller
    gets the True/False back so it can decide whether a fallback post is warranted
    (see jobs/remediation_job.py, which falls back to SLACK_WEBHOOK_URL as a fresh
    message once every row for an incident is terminal and this update failed).
    """
    payload = _build_message(
        incident_id=incident_id, title=title, risk_tier=risk_tier, description=description, remediations=remediations
    )
    try:
        await _post(response_url, payload, log_context=f"remediation update for incident_id={incident_id}")
        return True
    except httpx.HTTPError:
        logger.exception("failed to post remediation update to Slack response_url for incident_id=%s", incident_id)
        return False
