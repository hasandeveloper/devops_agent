"""verify_sns_signature guards POST /webhooks/cloudwatch's SNS delivery -- the entry
point for every CloudWatch alarm this agent diagnoses, and therefore the highest-stakes
of the three signature checks in verifiable.py. Same approach as test_slack_signature.py:
sign with a real, locally-generated key pair and monkeypatch the network-fetching step
(load_signing_public_key) so these stay fast and offline while still exercising the
actual cryptographic verification, not just a mocked-out stand-in for it.
"""

import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException

from app.controllers.concerns.webhooks import verifiable
from app.controllers.concerns.webhooks.verifiable import canonical_string_to_sign, verify_sns_signature

_VALID_CERT_URL = "https://sns.ap-south-1.amazonaws.com/SimpleNotificationService-" + "a" * 32 + ".pem"


def _base_message(**overrides) -> dict:
    message = {
        "Type": "Notification",
        "MessageId": "11111111-1111-1111-1111-111111111111",
        "TopicArn": "arn:aws:sns:ap-south-1:123456789012:test-topic",
        "Subject": "test",
        "Message": '{"AlarmName": "test alarm"}',
        "Timestamp": "2026-08-25T00:00:00.000Z",
        "SignatureVersion": "2",
        "SigningCertURL": _VALID_CERT_URL,
    }
    message.update(overrides)
    return message


def _sign(private_key, message: dict) -> str:
    canonical = canonical_string_to_sign(message).encode("utf-8")
    signature = private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _stub_cert_fetch(monkeypatch, keypair):
    _, public_key = keypair

    async def _fake_load(cert_url: str):
        return public_key

    monkeypatch.setattr(verifiable, "load_signing_public_key", _fake_load)


async def test_accepts_a_correctly_signed_notification(keypair):
    private_key, _ = keypair
    message = _base_message()
    message["Signature"] = _sign(private_key, message)

    await verify_sns_signature(message)  # does not raise


async def test_rejects_an_untrusted_signing_cert_url(monkeypatch, keypair):
    private_key, _ = keypair
    message = _base_message(SigningCertURL="https://evil.com/SimpleNotificationService-" + "a" * 32 + ".pem")
    message["Signature"] = _sign(private_key, message)

    async def _fail_if_called(cert_url: str):
        raise AssertionError("should never fetch a cert for an untrusted SigningCertURL")

    monkeypatch.setattr(verifiable, "load_signing_public_key", _fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await verify_sns_signature(message)
    assert exc_info.value.status_code == 401
    assert "untrusted" in exc_info.value.detail


async def test_rejects_an_incorrect_signature():
    message = _base_message()
    message["Signature"] = base64.b64encode(b"not a real signature").decode()

    with pytest.raises(HTTPException) as exc_info:
        await verify_sns_signature(message)
    assert exc_info.value.status_code == 401


async def test_rejects_a_signature_computed_for_a_different_message(keypair):
    private_key, _ = keypair
    message = _base_message()
    message["Signature"] = _sign(private_key, message)

    tampered = dict(message)
    tampered["Message"] = '{"AlarmName": "a different alarm entirely"}'

    with pytest.raises(HTTPException) as exc_info:
        await verify_sns_signature(tampered)
    assert exc_info.value.status_code == 401


async def test_rejects_an_unsupported_signature_version(keypair):
    private_key, _ = keypair
    message = _base_message(SignatureVersion="3")
    message["Signature"] = _sign(private_key, message)

    with pytest.raises(HTTPException) as exc_info:
        await verify_sns_signature(message)
    assert exc_info.value.status_code == 401
    assert "unsupported SignatureVersion" in exc_info.value.detail


async def test_rejects_an_unsupported_message_type():
    # canonical_string_to_sign raises ValueError for this Type before a signature is
    # ever meaningfully checked against it -- the Signature value itself doesn't matter.
    message = _base_message(Type="SomethingUnexpected")
    message["Signature"] = base64.b64encode(b"irrelevant").decode()

    with pytest.raises(HTTPException) as exc_info:
        await verify_sns_signature(message)
    assert exc_info.value.status_code == 401
