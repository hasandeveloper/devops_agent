"""verify_slack_signature guards POST /webhooks/slack/interactions -- the endpoint that
approves a remediation and can trigger a real pg_cancel_backend call. Unlike
verify_github_signature, a missing signing secret is treated as a hard failure here
rather than "verification disabled", since this endpoint can cause a write action.
"""

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException

from app.controllers.concerns.webhooks.verifiable import verify_slack_signature
from config import settings


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-signing-secret")
    yield


def test_accepts_a_correctly_signed_request():
    body = b'payload={"actions":[]}'
    timestamp = str(time.time())
    signature = _sign(settings.slack_signing_secret, timestamp, body)

    verify_slack_signature(body, timestamp, signature)  # does not raise


def test_rejects_when_signing_secret_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    body = b"payload={}"
    timestamp = str(time.time())

    with pytest.raises(HTTPException) as exc_info:
        verify_slack_signature(body, timestamp, "v0=irrelevant")
    assert exc_info.value.status_code == 401


def test_rejects_an_incorrect_signature():
    body = b'payload={"actions":[]}'
    timestamp = str(time.time())

    with pytest.raises(HTTPException) as exc_info:
        verify_slack_signature(body, timestamp, "v0=" + "0" * 64)
    assert exc_info.value.status_code == 401


def test_rejects_a_stale_timestamp():
    body = b'payload={"actions":[]}'
    stale_timestamp = str(time.time() - 600)  # 10 minutes old, past the 5 minute window
    signature = _sign(settings.slack_signing_secret, stale_timestamp, body)

    with pytest.raises(HTTPException) as exc_info:
        verify_slack_signature(body, stale_timestamp, signature)
    assert exc_info.value.status_code == 401


def test_rejects_a_non_numeric_timestamp():
    body = b'payload={"actions":[]}'
    signature = _sign(settings.slack_signing_secret, "not-a-number", body)

    with pytest.raises(HTTPException) as exc_info:
        verify_slack_signature(body, "not-a-number", signature)
    assert exc_info.value.status_code == 401


def test_rejects_a_signature_computed_for_a_different_body():
    timestamp = str(time.time())
    signature = _sign(settings.slack_signing_secret, timestamp, b'payload={"actions":[]}')

    with pytest.raises(HTTPException) as exc_info:
        verify_slack_signature(b'payload={"actions":[{"tampered":true}]}', timestamp, signature)
    assert exc_info.value.status_code == 401
