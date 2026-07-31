"""Negative-control fixture for scripts/check_ai_trailer.py.

This reproduces, as an isolated automated test, the rejection scenario
originally demonstrated on a live pull request (PR #16, commit f2785a4)
to prove the AI-authorship gate fails closed on a commit missing the
required trailers. Running it here means the rejection path is
regression-tested on every CI run without ever landing a
non-compliant commit in this repository's real history.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_ai_trailer.py"

COMPLIANT_TRAILERS = (
    "Generated-by: Bumble\n"
    "Co-authored-by: Osman Orka <15340@coderacademy.edu.au>\n"
    "Signed-off-by: Osman Orka <15340@coderacademy.edu.au>\n"
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, filename: str, message: str) -> None:
    (repo / filename).write_text(f"{filename} content\n")
    _run_git(repo, "add", filename)
    _run_git(repo, "commit", "-m", message)


def _run_check(repo: Path, base_ref: str, head_ref: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), base_ref, head_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.name", "Test Bot")
    _run_git(repo, "config", "user.email", "test-bot@example.invalid")
    _commit(repo, "base.txt", "chore: base commit\n\n" + COMPLIANT_TRAILERS)
    _run_git(repo, "branch", "main")
    return repo


def test_rejects_commit_missing_all_trailers(git_repo: Path) -> None:
    """Reproduces PR #16 / commit f2785a4: a commit with zero trailers fails closed."""
    _run_git(git_repo, "checkout", "-q", "-b", "feature")
    _commit(git_repo, "change.txt", "fix: deliberately non-compliant commit")

    result = _run_check(git_repo, "main", "feature")

    assert result.returncode == 1
    assert "AI-authorship trailer check FAILED" in result.stdout
    assert "missing Generated-by, Co-authored-by, Signed-off-by" in result.stdout


def test_rejects_commit_missing_one_trailer(git_repo: Path) -> None:
    _run_git(git_repo, "checkout", "-q", "-b", "feature")
    _commit(
        git_repo,
        "change.txt",
        "fix: partial trailers\n\n"
        "Generated-by: Bumble\n"
        "Co-authored-by: Osman Orka <15340@coderacademy.edu.au>\n",
    )

    result = _run_check(git_repo, "main", "feature")

    assert result.returncode == 1
    assert "missing Signed-off-by" in result.stdout


def test_passes_when_every_commit_carries_all_trailers(git_repo: Path) -> None:
    _run_git(git_repo, "checkout", "-q", "-b", "feature")
    _commit(git_repo, "change.txt", "fix: compliant commit\n\n" + COMPLIANT_TRAILERS)

    result = _run_check(git_repo, "main", "feature")

    assert result.returncode == 0
    assert "AI-authorship trailer check passed for 1 commit(s)." in result.stdout


def test_mixed_range_reports_only_the_noncompliant_commit(git_repo: Path) -> None:
    """A range with one good and one bad commit must fail, naming the bad one only."""
    _run_git(git_repo, "checkout", "-q", "-b", "feature")
    _commit(git_repo, "good.txt", "fix: compliant commit\n\n" + COMPLIANT_TRAILERS)
    bad_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    )
    _commit(git_repo, "bad.txt", "fix: non-compliant commit")

    result = _run_check(git_repo, "main", "feature")

    assert result.returncode == 1
    assert bad_sha.stdout.strip()[:12] not in result.stdout
    assert "missing Generated-by, Co-authored-by, Signed-off-by" in result.stdout
