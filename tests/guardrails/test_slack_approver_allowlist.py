"""_is_authorized_approver guards Approve/Approve All Remaining on
POST /webhooks/slack/interactions. verify_slack_signature only proves a click came
from Slack -- it says nothing about whether the clicking user is allowed to trigger
a write action (pg_cancel_backend/pg_terminate_backend). This is that missing check,
and it fails closed: an unconfigured allowlist means nobody is authorized, not
"check disabled" (unlike verify_github_signature's blank-secret behavior).
"""

import pytest

from app.controllers.webhooks import _is_authorized_approver
from config import settings


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", "")
    yield


def test_rejects_everyone_when_allowlist_is_blank(monkeypatch):
    assert _is_authorized_approver({"id": "U123", "username": "jane.doe"}) is False


def test_accepts_a_user_whose_id_is_listed(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", "U123,U456")
    assert _is_authorized_approver({"id": "U123", "username": "jane.doe"}) is True


def test_accepts_a_user_whose_username_is_listed(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", "jane.doe")
    assert _is_authorized_approver({"id": "U999", "username": "jane.doe"}) is True


def test_match_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", "Jane.Doe")
    assert _is_authorized_approver({"id": "U999", "username": "jane.doe"}) is True


def test_rejects_a_user_not_on_the_list(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", "U123")
    assert _is_authorized_approver({"id": "U999", "username": "someone.else"}) is False


def test_tolerates_whitespace_and_empty_entries_in_the_list(monkeypatch):
    monkeypatch.setattr(settings, "slack_approver_allowlist", " U123 , , U456 ")
    assert _is_authorized_approver({"id": "U123"}) is True
