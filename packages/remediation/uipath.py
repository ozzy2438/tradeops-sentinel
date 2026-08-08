"""Ephemeral launch credentials for the attended UiPath browser boundary.

The raw token is returned once to the authenticated operator and is never
persisted.  PostgreSQL stores only its SHA-256 digest beside an expiry.  The
token is not a replacement for Maker/Checker approval or envelope signing;
it only proves that the browser holding it came from an explicitly prepared
attended run.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class UiPathLaunchCredential:
    run_id: str
    token: str
    token_digest: str


def token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_launch_credential() -> UiPathLaunchCredential:
    token = secrets.token_urlsafe(32)
    return UiPathLaunchCredential(
        run_id=f"uipath_run_{secrets.token_hex(16)}",
        token=token,
        token_digest=token_digest(token),
    )


def token_matches(token: str, expected_digest: str) -> bool:
    return hmac.compare_digest(token_digest(token), expected_digest)


__all__ = [
    "UiPathLaunchCredential",
    "issue_launch_credential",
    "token_digest",
    "token_matches",
]
