import json
import logging
import uuid
from urllib.parse import parse_qs

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from config import settings
from config.reliability.rate_limiter import is_rate_limited
from app.models import EventSource, Incident, RemediationStatus
from app.services.ingestion_service import store_raw_event
from app.services.remediation_service import (
    action_to_dict,
    decide_all_proposed,
    decide_remediation,
    get_remediation,
    get_remediations_for_incident,
    recompute_incident_status,
)
from app.services.slack_service import post_remediation_update
from jobs.remediation_job import execute_remediation_job
from jobs.webhooks_job import aws_sns_event_job
from app.controllers.concerns.webhooks.verifiable import (
    is_trusted_sns_url,
    verify_github_signature,
    verify_slack_signature,
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


async def _rerender_and_notify(db: Session, response_url: str, incident_id: uuid.UUID) -> None:
    """Recomputes the incident's overall status from its remediation rows, then rebuilds
    and posts the full message -- see slack_service.post_remediation_update's docstring
    for why this replaces everything rather than patching just the clicked row.
    """
    recompute_incident_status(db, incident_id)
    incident = db.get(Incident, incident_id)
    remediations = [action_to_dict(a) for a in get_remediations_for_incident(db, incident_id)]
    risk_tier = incident.risk_tier.value if hasattr(incident.risk_tier, "value") else incident.risk_tier
    await post_remediation_update(
        response_url,
        incident_id=str(incident_id),
        title=incident.title,
        risk_tier=risk_tier,
        description=incident.description,
        remediations=remediations,
    )


async def handle_slack_interaction(request: Request, db: Session) -> dict:
    """Handles a click on the Approve/Reject/Approve-All buttons notify_slack.py posts
    for a proposed remediation (see app/services/slack_service.py's block rendering).

    This is the human-approval half of the HITL remediation flow -- an approve here
    enqueues execute_remediation_job (jobs/remediation_job.py), which is the only place
    a write action (pg_cancel_backend) actually runs. Everything in this function itself
    stays read/write against Postgres bookkeeping only, fast enough to respond within
    Slack's ~3 second ack window.
    """
    raw_body = await request.body()
    verify_slack_signature(
        raw_body,
        request.headers.get("x-slack-request-timestamp", ""),
        request.headers.get("x-slack-signature", ""),
    )

    form = parse_qs(raw_body.decode())
    payload_values = form.get("payload")
    if not payload_values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing payload")

    payload = json.loads(payload_values[0])
    action = payload["actions"][0]
    action_id = action["action_id"]
    value = action["value"]
    response_url = payload["response_url"]
    user = payload.get("user", {})
    decided_by = user.get("username") or user.get("name") or user.get("id", "unknown")

    if action_id == "approve_all_remediations":
        incident_id = uuid.UUID(value)
        approved = decide_all_proposed(
            db, incident_id, status=RemediationStatus.approved, decided_by=decided_by, response_url=response_url
        )
        for action_row in approved:
            execute_remediation_job.delay(str(action_row.id))

    elif action_id in ("approve_remediation", "reject_remediation"):
        remediation_id = uuid.UUID(value)
        existing = get_remediation(db, remediation_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown remediation")
        incident_id = existing.incident_id

        new_status = RemediationStatus.approved if action_id == "approve_remediation" else RemediationStatus.rejected
        decided = decide_remediation(db, remediation_id, status=new_status, decided_by=decided_by, response_url=response_url)
        if decided is not None and new_status == RemediationStatus.approved:
            execute_remediation_job.delay(str(decided.id))

    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown action_id={action_id!r}")

    await _rerender_and_notify(db, response_url, incident_id)

    return {"status": "ok"}
