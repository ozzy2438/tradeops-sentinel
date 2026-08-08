"""Pure tests for the attended UiPath launch credential boundary."""

from packages.remediation.uipath import (
    issue_launch_credential,
    token_digest,
    token_matches,
)


def test_launch_credential_is_high_entropy_and_persists_only_a_digest() -> None:
    credential = issue_launch_credential()

    assert credential.run_id.startswith("uipath_run_")
    assert len(credential.token) >= 40
    assert credential.token not in credential.token_digest
    assert credential.token_digest.startswith("sha256:")
    assert credential.token_digest == token_digest(credential.token)


def test_launch_token_comparison_fails_closed() -> None:
    credential = issue_launch_credential()

    assert token_matches(credential.token, credential.token_digest)
    assert not token_matches("wrong-token-value", credential.token_digest)
