import httpx

from config import settings


async def post_diagnosis(*, incident_id: str, diagnosis: dict) -> None:
    if not settings.slack_webhook_url:
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
