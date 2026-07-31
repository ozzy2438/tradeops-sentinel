#!/usr/bin/env python3
"""Enforce the AI-authorship transparency convention (CONTRIBUTING.md).

Every commit in the pull request's range must carry all three trailers:
  Generated-by:  <agent name>
  Co-authored-by: <Name> <email>
  Signed-off-by:  <Name> <email>

Usage:
  check_ai_trailer.py <base-ref> <head-ref>

Exits non-zero and prints the offending commit(s) if any commit in
(base-ref, head-ref] is missing a required trailer. This intentionally
requires the trailers on every commit rather than trying to guess which
commits were "AI-assisted" — during this project's current phase all
implementation commits are agent-authored, so the blanket rule is both
simpler and honest.
"""

from __future__ import annotations

import re
import subprocess
import sys

REQUIRED_TRAILERS = ("Generated-by", "Co-authored-by", "Signed-off-by")
TRAILER_LINE_RE = re.compile(r"^([A-Za-z-]+):\s*(.+)$")


def commit_shas(base_ref: str, head_ref: str) -> list[str]:
    out = subprocess.run(
        ["git", "rev-list", f"{base_ref}..{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def commit_trailers(sha: str) -> dict[str, str]:
    out = subprocess.run(
        ["git", "show", "-s", "--format=%B", sha],
        capture_output=True,
        text=True,
        check=True,
    )
    trailers: dict[str, str] = {}
    for line in out.stdout.splitlines():
        m = TRAILER_LINE_RE.match(line.strip())
        if m and m.group(1) in REQUIRED_TRAILERS:
            trailers[m.group(1)] = m.group(2).strip()
    return trailers


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    base_ref, head_ref = sys.argv[1], sys.argv[2]
    shas = commit_shas(base_ref, head_ref)
    if not shas:
        print("No commits in range — nothing to check.")
        return 0

    failures: list[tuple[str, list[str]]] = []
    for sha in shas:
        trailers = commit_trailers(sha)
        missing = [t for t in REQUIRED_TRAILERS if t not in trailers or not trailers[t]]
        if missing:
            failures.append((sha, missing))

    if failures:
        print("AI-authorship trailer check FAILED:")
        for sha, missing in failures:
            print(f"  {sha[:12]}: missing {', '.join(missing)}")
        print(
            "\nEvery commit must carry Generated-by / Co-authored-by / "
            "Signed-off-by trailers. See CONTRIBUTING.md."
        )
        return 1

    print(f"AI-authorship trailer check passed for {len(shas)} commit(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
