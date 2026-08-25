import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident, IncidentStatus, RemediationAction, RemediationStatus


def create_remediation_action(
    db: Session,
    *,
    incident_id: uuid.UUID,
    action_type: str,
    environment: str,
    target_pid: int,
    target_query: str,
    target_duration_seconds: float,
    rationale: str,
    target_backend_start: float | None = None,
) -> RemediationAction:
    action = RemediationAction(
        incident_id=incident_id,
        action_type=action_type,
        environment=environment,
        target_pid=target_pid,
        target_query=target_query,
        target_duration_seconds=target_duration_seconds,
        target_backend_start=target_backend_start,
        rationale=rationale,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def action_to_dict(action: RemediationAction) -> dict:
    """The shape app/services/slack_service.py's block-rendering functions expect --
    shared by propose_remediation.py (initial post), the Slack interaction handler,
    and jobs/remediation_job.py, so all three render from the same field mapping.
    """
    return {
        "id": str(action.id),
        "action_type": action.action_type,
        "pid": action.target_pid,
        "duration_seconds": action.target_duration_seconds,
        "query": action.target_query,
        "rationale": action.rationale,
        "status": action.status.value,
        "decided_by": action.decided_by,
        "result": action.result,
    }


def get_remediation(db: Session, remediation_id: uuid.UUID) -> RemediationAction | None:
    return db.get(RemediationAction, remediation_id)


def get_remediations_for_incident(db: Session, incident_id: uuid.UUID) -> list[RemediationAction]:
    return list(
        db.execute(select(RemediationAction).filter_by(incident_id=incident_id).order_by(RemediationAction.created_at))
        .scalars()
        .all()
    )


def decide_remediation(
    db: Session,
    remediation_id: uuid.UUID,
    *,
    status: RemediationStatus,
    decided_by: str,
    response_url: str | None,
) -> RemediationAction | None:
    """Record a human's approve/reject decision on one row.

    Returns None (a no-op) if the row isn't still `proposed` -- guards a double-click
    (e.g. the same Approve button clicked twice, or Approve then Reject) from re-deciding
    or re-enqueueing execution for an already-decided row.
    """
    action = db.get(RemediationAction, remediation_id)
    if action is None or action.status != RemediationStatus.proposed:
        return None

    action.status = status
    action.decided_by = decided_by
    action.decided_at = datetime.now(timezone.utc)
    if response_url is not None:
        action.slack_response_url = response_url
    db.commit()
    db.refresh(action)
    return action


def decide_all_proposed(
    db: Session,
    incident_id: uuid.UUID,
    *,
    status: RemediationStatus,
    decided_by: str,
    response_url: str | None,
) -> list[RemediationAction]:
    """Approve (or reject) every row still `proposed` for an incident -- backs the
    "Approve All Remaining" button. Returns the rows just decided, so the caller
    knows which ones to enqueue for execution.
    """
    proposed = [a for a in get_remediations_for_incident(db, incident_id) if a.status == RemediationStatus.proposed]
    decided_at = datetime.now(timezone.utc)
    for action in proposed:
        action.status = status
        action.decided_by = decided_by
        action.decided_at = decided_at
        if response_url is not None:
            action.slack_response_url = response_url
    db.commit()
    for action in proposed:
        db.refresh(action)
    return proposed


def mark_remediation_result(
    db: Session, remediation_id: uuid.UUID, *, status: RemediationStatus, result: str
) -> RemediationAction:
    action = db.get(RemediationAction, remediation_id)
    action.status = status
    action.result = result
    action.executed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(action)
    return action


def recompute_incident_status(db: Session, incident_id: uuid.UUID) -> IncidentStatus:
    """Derive the incident's overall status from its remediation rows.

    Any row still pending a decision or awaiting execution keeps the incident at
    awaiting_approval. Once nothing is pending: resolved if at least one row was
    actually executed (a partially-approved incident -- some rows rejected, some
    approved -- settles to resolved once its approved rows finish, since the human
    addressed what needed addressing); rejected only if every row was rejected.
    """
    actions = get_remediations_for_incident(db, incident_id)
    pending = {RemediationStatus.proposed, RemediationStatus.approved}
    if any(a.status in pending for a in actions):
        status = IncidentStatus.awaiting_approval
    elif any(a.status == RemediationStatus.executed for a in actions):
        status = IncidentStatus.resolved
    else:
        status = IncidentStatus.rejected

    incident = db.get(Incident, incident_id)
    incident.status = status
    db.commit()
    return status
