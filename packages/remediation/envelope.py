"""Signed, expiring, idempotent action envelope.

The envelope is the only thing the executor trusts. Its content hash and
signature are computed over every field except themselves, using
``model_dump(mode="json")`` on the actual ``ActionEnvelope`` instance for
both signing and verification, so there is exactly one canonical
serialisation and no risk of a build/verify format mismatch producing a false
tamper detection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta

from .models import ActionEnvelope

ENVELOPE_TTL_SECONDS = 900  # 15 minutes
SIGNING_SECRET_ENV_VAR = "TRADEOPS_REMEDIATION_SIGNING_SECRET"


class EnvelopeSigningError(RuntimeError):
    """The signing secret is not configured."""


class EnvelopeTamperedError(RuntimeError):
    """The envelope's content or signature does not verify."""


class EnvelopeExpiredError(RuntimeError):
    """The envelope's expires_at has passed."""


def _signing_secret(secret: str | None) -> str:
    if secret is not None:
        return secret
    configured = os.getenv(SIGNING_SECRET_ENV_VAR, "")
    if not configured:
        raise EnvelopeSigningError(f"{SIGNING_SECRET_ENV_VAR} is not configured")
    return configured


def _canonical_bytes(fields: dict[str, object]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _content_hash_of(envelope: ActionEnvelope) -> str:
    fields = envelope.model_dump(mode="json", exclude={"content_hash", "signature"})
    return "sha256:" + hashlib.sha256(_canonical_bytes(fields)).hexdigest()


def sign_content_hash(content_hash: str, *, secret: str | None = None) -> str:
    key = _signing_secret(secret).encode("utf-8")
    return hmac.new(key, content_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def build_envelope(
    *,
    case_id: str,
    trade_id: str,
    tenant_id: str,
    portfolio_id: str,
    field_path: str,
    approved_value: str,
    expected_old_value: str,
    maker_identity: str,
    checker_identity: str,
    idempotency_key: str,
    issued_at: datetime | None = None,
    ttl_seconds: int = ENVELOPE_TTL_SECONDS,
    secret: str | None = None,
) -> ActionEnvelope:
    issued = issued_at or datetime.now(UTC)
    expires = issued + timedelta(seconds=ttl_seconds)
    draft = ActionEnvelope(
        case_id=case_id,
        trade_id=trade_id,
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        action_type="CORRECT_LEGACY_BOOKING_FIELD",
        field_path=field_path,
        approved_value=approved_value,
        expected_old_value=expected_old_value,
        maker_identity=maker_identity,
        checker_identity=checker_identity,
        issued_at=issued,
        expires_at=expires,
        idempotency_key=idempotency_key,
        content_hash="sha256:" + "0" * 64,
        signature="",
    )
    content_hash = _content_hash_of(draft)
    signature = sign_content_hash(content_hash, secret=secret)
    return draft.model_copy(update={"content_hash": content_hash, "signature": signature})


def verify_envelope(
    envelope: ActionEnvelope,
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raise closed on any of: tampered content, invalid signature, expiry.

    Order matters for a clear error, but all three are checked -- tamper
    detection always runs before the expiry check, so a modified-and-expired
    envelope is reported as tampered, the more serious condition.
    """

    recomputed_hash = _content_hash_of(envelope)
    if recomputed_hash != envelope.content_hash:
        raise EnvelopeTamperedError("envelope content does not match its content_hash")
    expected_signature = sign_content_hash(envelope.content_hash, secret=secret)
    if not hmac.compare_digest(expected_signature, envelope.signature):
        raise EnvelopeTamperedError("envelope signature is invalid")
    current = now or datetime.now(UTC)
    if current >= envelope.expires_at:
        raise EnvelopeExpiredError(f"envelope expired at {envelope.expires_at.isoformat()}")


__all__ = [
    "ENVELOPE_TTL_SECONDS",
    "SIGNING_SECRET_ENV_VAR",
    "EnvelopeExpiredError",
    "EnvelopeSigningError",
    "EnvelopeTamperedError",
    "build_envelope",
    "sign_content_hash",
    "verify_envelope",
]
