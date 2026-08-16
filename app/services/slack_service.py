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


async def post_diagnosis(*, incident_id: str, diagnosis: dict, query_evidence: list[dict] | None = None) -> None:
    if not settings.slack_webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set, skipping notification for incident_id=%s", incident_id)
        return

    text = (
        f"*{diagnosis['title']}*\n"
        f"Risk: {diagnosis['risk_tier']}\n"
        f"{diagnosis['description']}"
        f"{_format_query_evidence(query_evidence)}\n"
        f"Incident: {incident_id}"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()

    logger.info("posted diagnosis to Slack for incident_id=%s", incident_id)
