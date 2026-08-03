#!/usr/bin/env python3
"""Fail closed on placeholder commit author/committer identities.

CONTRIBUTING.md requires every commit to carry the real, verified email of the
human operator, and explicitly forbids committing "with a guessed or
example.com-style address". ``check_ai_trailer.py`` only parses the commit
*message* trailers, so a commit could satisfy every required trailer while its
git author/committer identity was still a reserved documentation address --
which is exactly what happened on this branch before the identity rewrite.

This check closes that gap by rejecting reserved/placeholder domains (RFC 2606
and RFC 6761 reserved names, plus obviously-unset local defaults) in either the
author or the committer field of every commit in the pull request's range.

Usage:
  check_commit_identity.py <base-ref> <head-ref>

Exits non-zero and prints every offending commit and field.
"""

from __future__ import annotations

import subprocess
import sys

# RFC 2606 reserved TLDs/second-level names and RFC 6761 special-use names.
# These can never be a real deliverable contributor address.
FORBIDDEN_DOMAINS: frozenset[str] = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "localhost",
        "localhost.localdomain",
        "invalid",
        "test",
        "local",
    }
)
FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".invalid",
    ".test",
    ".localhost",
    ".local",
)

_FIELDS: tuple[tuple[str, str], ...] = (("author", "%ae"), ("committer", "%ce"))


def commit_shas(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "rev-list", f"{base_ref}..{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def commit_email(sha: str, placeholder: str) -> str:
    result = subprocess.run(
        ["git", "show", "-s", f"--format={placeholder}", sha],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def is_forbidden(email: str) -> bool:
    if not email or "@" not in email:
        return True
    domain = email.rpartition("@")[2].strip().lower().rstrip(".")
    if not domain:
        return True
    if domain in FORBIDDEN_DOMAINS:
        return True
    return domain.endswith(FORBIDDEN_SUFFIXES)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    base_ref, head_ref = sys.argv[1], sys.argv[2]
    shas = commit_shas(base_ref, head_ref)
    if not shas:
        print("No commits in range — nothing to check.")
        return 0

    failures: list[tuple[str, str, str]] = []
    for sha in shas:
        for field, placeholder in _FIELDS:
            email = commit_email(sha, placeholder)
            if is_forbidden(email):
                failures.append((sha, field, email))

    if failures:
        print("Commit identity check FAILED:")
        for sha, field, email in failures:
            print(f"  {sha[:12]}: {field} email {email!r} is a placeholder/reserved address")
        print(
            "\nEvery commit must use the operator's real, verified email in both "
            "the author and committer fields. See CONTRIBUTING.md."
        )
        return 1

    print(f"Commit identity check passed for {len(shas)} commit(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
