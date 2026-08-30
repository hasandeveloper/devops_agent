"""_outcome_for decides what a write tool's raw result actually means. Confirmed live
that pg_cancel_backend/pg_terminate_backend can return signal_sent=false even when
action_taken=True (the pre-check passed and the call was made, but Postgres itself
says the signal wasn't delivered) -- before this, that case was silently reported the
same as a real success ("cancelled"/"terminated"), which is a lie a human approver
would have no way to catch from Slack alone.
"""

from app.models import RemediationStatus
from jobs.remediation_job import _outcome_for


def test_action_not_taken_is_a_skip_not_a_failure():
    result = {"action_taken": False, "skipped": "pid no longer present -- query likely finished"}
    status, text = _outcome_for(result, "cancelled", "cancel_backend")
    assert status == RemediationStatus.executed
    assert text == "skipped: pid no longer present -- query likely finished"


def test_signal_sent_true_is_a_real_success():
    result = {"action_taken": True, "signal_sent": True}
    status, text = _outcome_for(result, "cancelled", "cancel_backend")
    assert status == RemediationStatus.executed
    assert text == "cancelled"


def test_signal_sent_false_is_reported_as_failed_not_executed():
    result = {"action_taken": True, "signal_sent": False}
    status, text = _outcome_for(result, "cancelled", "cancel_backend")
    assert status == RemediationStatus.failed
    assert "signal_sent=false" in text
    assert "cancel_backend" in text


def test_terminate_backend_uses_its_own_verb_and_tool_name():
    result = {"action_taken": True, "signal_sent": False}
    status, text = _outcome_for(result, "terminated", "terminate_backend")
    assert status == RemediationStatus.failed
    assert "terminate_backend" in text
