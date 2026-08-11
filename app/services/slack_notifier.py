import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def post_diagnosis(*, incident_id: str, diagnosis: dict) -> None:
    if not settings.slack_webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set, skipping notification for incident_id=%s", incident_id)
        return

    text = (
        f"*{diagnosis['title']}*\n"
        f"Risk: {diagnosis['risk_tier']}\n"
        f"{diagnosis['description']}\n"
        f"Incident: {incident_id}"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()

    logger.info("posted diagnosis to Slack for incident_id=%s", incident_id)
