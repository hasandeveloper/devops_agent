import base64
import binascii
import hashlib
import hmac
import re
from urllib.parse import urlparse

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import HTTPException, status

from config import settings

# Per AWS's documented cert URL format -- rejects a signature check that would
# otherwise fetch (and trust) a certificate from an attacker-controlled host.
_SIGNING_CERT_URL_RE = re.compile(
    r"^https://sns\.[a-zA-Z0-9\-]{3,}\.amazonaws\.com(\.cn)?/SimpleNotificationService-[a-zA-Z0-9]{32}\.pem$"
)

# Field order AWS signs over, per message type. See "Verifying the signatures
# of Amazon SNS messages" in the AWS docs -- order and exact field names matter.
_SIGNED_FIELDS_BY_TYPE = {
    "Notification": (
        "Message",
        "MessageId",
        "Subject",
        "Timestamp",
        "TopicArn",
        "Type",
    ),
    "SubscriptionConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
    "UnsubscribeConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
}

# AWS signing certs are effectively immutable once published; caching avoids
# re-fetching the same cert on every notification.
_signing_cert_cache: dict[str, bytes] = {}


def is_trusted_sns_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith(".amazonaws.com")


async def fetch_signing_cert(cert_url: str) -> bytes:
    if cert_url not in _signing_cert_cache:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(cert_url)
            response.raise_for_status()

        _signing_cert_cache[cert_url] = response.content

    return _signing_cert_cache[cert_url]


async def load_signing_public_key(cert_url: str):
    cert_bytes = await fetch_signing_cert(cert_url)
    return x509.load_pem_x509_certificate(cert_bytes).public_key()


def canonical_string_to_sign(message: dict) -> str:
    fields = _SIGNED_FIELDS_BY_TYPE.get(message.get("Type"))

    if fields is None:
        raise ValueError(
            f"unsupported message type for signing: {message.get('Type')}"
        )

    parts = []

    for field in fields:
        if field in message:
            parts.append(field)
            parts.append(str(message[field]))

    return "\n".join(parts) + "\n"


def get_hash_algorithm(signature_version: str):
    if signature_version == "1":
        return hashes.SHA1()

    if signature_version == "2":
        return hashes.SHA256()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"unsupported SignatureVersion: {signature_version}",
    )


async def verify_sns_signature(message: dict) -> None:
    cert_url = message.get("SigningCertURL", "")

    if not _SIGNING_CERT_URL_RE.match(cert_url):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="untrusted SigningCertURL",
        )

    try:
        public_key = await load_signing_public_key(cert_url)

        signature = base64.b64decode(message["Signature"])

        canonical_message = canonical_string_to_sign(message).encode("utf-8")

        hash_algorithm = get_hash_algorithm(
            message.get("SignatureVersion", "")
        )

        public_key.verify(
            signature,
            canonical_message,
            padding.PKCS1v15(),
            hash_algorithm,
        )

    except (
        KeyError,
        ValueError,
        InvalidSignature,
        httpx.HTTPError,
        binascii.Error,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid SNS signature",
        ) from exc


def verify_github_signature(raw_body: bytes, signature_header: str) -> None:
    if not settings.github_webhook_secret:
        return

    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature_header, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signature",
        )