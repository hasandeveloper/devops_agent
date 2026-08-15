import json
import logging

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from config import settings
from config.rate_limiter import is_rate_limited
from app.models import EventSource
from app.services.ingestion import store_raw_event
from jobs.webhooks_job import aws_sns_event_job
from app.controllers.concerns.webhooks.verifiable import (
    is_trusted_sns_url,
    verify_github_signature,
    verify_sns_signature,
)

logger = logging.getLogger(__name__)

_RESOURCE_DIMENSION_NAMES = {
    "ClusterName",
    "ServiceName",
    "DBInstanceIdentifier",
    "DBClusterIdentifier",
    "LoadBalancer",
    "TargetGroup",
    "AutoScalingGroupName",
    "InstanceId",
    "VolumeId",
}


def _extract_resource_id(alarm_payload: dict) -> str | None:
    dimensions = alarm_payload.get("Trigger", {}).get("Dimensions", [])
    for dim in dimensions:
        if dim.get("name") in _RESOURCE_DIMENSION_NAMES:
            return dim.get("value")
    return None


async def handle_sns_control_message(
    message_type: str,
    message: dict,
) -> dict | None:
    """Handle SNS lifecycle messages.

    Returns:
        dict: Response for handled control messages.
        None: If this is a Notification and processing should continue.
    """

    if message_type == "SubscriptionConfirmation":
        subscribe_url = message.get("SubscribeURL", "")

        if settings.sns_auto_confirm_subscriptions and subscribe_url:
            if not is_trusted_sns_url(subscribe_url):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="untrusted SubscribeURL host",
                )

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(subscribe_url)
                response.raise_for_status()

            logger.info(
                "Confirmed SNS subscription for topic %s",
                message.get("TopicArn"),
            )

        return {"status": "subscription_confirmation_handled"}

    if message_type == "UnsubscribeConfirmation":
        return {"status": "unsubscribe_acknowledged"}

    if message_type != "Notification":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported message type: {message_type}",
        )

    # Notification → continue normal processing
    return None

async def handle_cloudwatch_webhook(request: Request, db: Session) -> dict:
    # Checked before anything else -- cheapest possible rejection for a flood (real or
    # malicious) before spending any effort parsing/verifying/storing it. One shared key
    # across all callers: this guards raw request volume to this endpoint, not any one
    # sender's fair share of it.
    rate_limited = is_rate_limited(
        "rate_limit:cloudwatch_webhook",
        limit=settings.webhook_rate_limit,
        window_seconds=settings.webhook_rate_limit_window_seconds,
    )
    if rate_limited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many requests")

    raw_body = await request.body()
    try:
        message = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from exc

    await verify_sns_signature(message)

    message_type = request.headers.get(
        "x-amz-sns-message-type",
        message.get("Type"),
    )

    response = await handle_sns_control_message(
        message_type,
        message,
    )

    if response:
        return response

    try:
        alarm_payload = json.loads(message["Message"])
    except (KeyError, json.JSONDecodeError):
        alarm_payload = {"raw_message": message.get("Message")}

    event = store_raw_event(
        db,
        source=EventSource.cloudwatch,
        event_type=alarm_payload.get("AlarmName", "unknown_alarm"),
        resource_id=_extract_resource_id(alarm_payload),
        payload=alarm_payload,
    )

    aws_sns_event_job.delay(
        {"id": str(event.id), "source": str(event.source), "resource_id": event.resource_id, "payload": alarm_payload}
    )

    return {"status": "stored", "raw_event_id": str(event.id)}


async def handle_github_webhook(request: Request, db: Session) -> dict:
    raw_body = await request.body()
    verify_github_signature(raw_body, request.headers.get("x-hub-signature-256", ""))

    event_type = request.headers.get("x-github-event", "unknown")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from exc

    repo = payload.get("repository", {}).get("full_name")

    event = store_raw_event(
        db,
        source=EventSource.github_actions,
        event_type=event_type,
        resource_id=repo,
        payload=payload,
    )

    return {"status": "stored", "raw_event_id": str(event.id)}
