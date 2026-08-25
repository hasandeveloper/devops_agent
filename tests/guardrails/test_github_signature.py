"""verify_github_signature guards POST /webhooks/github. Deliberately asymmetric with
verify_slack_signature: a missing GITHUB_WEBHOOK_SECRET means verification is skipped,
not a hard failure (GitHub ingestion has no write-action consequence the way an
approved remediation does) -- these tests lock in that asymmetry so it can't regress
into either "always skip" or "always require" by accident.
"""

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.controllers.concerns.webhooks.verifiable import verify_github_signature
from config import settings


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "test-github-secret")
    yield


def test_accepts_a_correctly_signed_request():
    body = b'{"action": "completed"}'
    signature = _sign(settings.github_webhook_secret, body)

    verify_github_signature(body, signature)  # does not raise


def test_skips_verification_when_secret_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    body = b'{"action": "completed"}'

    verify_github_signature(body, "sha256=totally-not-a-real-signature")  # does not raise


def test_rejects_an_incorrect_signature():
    body = b'{"action": "completed"}'

    with pytest.raises(HTTPException) as exc_info:
        verify_github_signature(body, "sha256=" + "0" * 64)
    assert exc_info.value.status_code == 401


def test_rejects_a_signature_computed_for_a_different_body():
    signature = _sign(settings.github_webhook_secret, b'{"action": "completed"}')

    with pytest.raises(HTTPException) as exc_info:
        verify_github_signature(b'{"action": "tampered"}', signature)
    assert exc_info.value.status_code == 401


def test_rejects_a_malformed_signature_header():
    body = b'{"action": "completed"}'

    with pytest.raises(HTTPException) as exc_info:
        verify_github_signature(body, "not-even-the-right-format")
    assert exc_info.value.status_code == 401
