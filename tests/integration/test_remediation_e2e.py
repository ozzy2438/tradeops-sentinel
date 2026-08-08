"""End-to-end tests for the controlled-AI remediation slice.

Detection through structured AI triage, deterministic policy evaluation,
Maker+Checker approval, signed-envelope execution, idempotent replay and
timeout recovery, post-action reconciliation, and the frozen evidence record.

Requires a disposable PostgreSQL endpoint in ``TRADEOPS_TEST_DATABASE_URL``,
same as ``tests/integration/test_product_e2e.py``. Uses the deterministic
test AI provider (``TRADEOPS_AI_PROVIDER`` is left unset) -- no live LLM
credential is required or used anywhere in this module.

Item numbers in the comments below refer to the 15 required tests for this
slice; item 15 ("all existing tests remain green") is exercised by running
this module together with the rest of the suite, not by a test in this file.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")
fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

DATABASE_URL = os.getenv("TRADEOPS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="TRADEOPS_TEST_DATABASE_URL is required for remediation end-to-end tests",
)

API_KEY = "test-api-key-not-a-real-secret"
SIGNING_SECRET = "test-remediation-signing-secret-not-a-real-secret"

# The demo's single supported scenario: legacy booking's base_amount
# (1019000.00 EUR) diverges from FIX_EXECUTION's authoritative value
# (1018000.00 EUR). See packages/generator/core.py.
TARGET_TRADE_ID = "trade_20118124703fa8bb1c8ccc5c8c035285"
AUTHORITATIVE_VALUE = "1018000.00"
LEGACY_VALUE = "1019000.00"


@pytest.fixture(scope="module")
def api_env() -> Iterator[None]:
    previous = {
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "TRADEOPS_API_KEY": os.environ.get("TRADEOPS_API_KEY"),
        "TRADEOPS_REMEDIATION_SIGNING_SECRET": os.environ.get(
            "TRADEOPS_REMEDIATION_SIGNING_SECRET"
        ),
    }
    assert DATABASE_URL is not None
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["TRADEOPS_API_KEY"] = API_KEY
    os.environ["TRADEOPS_REMEDIATION_SIGNING_SECRET"] = SIGNING_SECRET
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="module")
def fresh_database(api_env: None) -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.commit()


@pytest.fixture(scope="module")
def client(fresh_database: None) -> Iterator[Any]:
    from apps.api.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def adapter(client: Any) -> Any:
    # Direct PostgresAdapter access, used only to exercise executor reject
    # paths the live API can never itself construct -- it only ever builds
    # a correctly-formed envelope from a case's own approved policy decision.
    from packages.persistence.adapter import PostgresAdapter

    assert DATABASE_URL is not None
    return PostgresAdapter(DATABASE_URL)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _approvals_from_store(store: Any, case_id: str) -> list[Any]:
    from packages.remediation.models import Approval

    return [
        Approval(
            role=row["role"],
            approver_identity=row["approver_identity"],
            decision=row["decision"],
            approved_recommendation_hash=row["approved_recommendation_hash"],
            decided_at=row["decided_at"],
        )
        for row in store.get_approvals(case_id)
    ]


# ---------------------------------------------------------------------
# 1. break detection
# ---------------------------------------------------------------------
def test_demo_data_loads_and_the_target_break_is_detected(client: Any) -> None:
    load = client.post("/demo/load", headers=_auth())
    assert load.status_code == 200, load.text

    run = client.post("/reconciliation/run", headers=_auth())
    assert run.status_code == 200, run.text
    assert run.json()["break_count"] > 0

    breaks = client.get(
        "/breaks",
        headers=_auth(),
        params={"break_family": "ECONOMIC_VALUE_MISMATCH", "limit": 500},
    ).json()
    target = next((row for row in breaks if row["trade_id"] == TARGET_TRADE_ID), None)
    assert target is not None, "the demo's single supported scenario trade was not detected"
    assert target["state"] == "OPEN"
    assert target["severity"] == "CRITICAL"


@pytest.fixture(scope="module")
def target_break_id(client: Any) -> str:
    breaks = client.get(
        "/breaks",
        headers=_auth(),
        params={"break_family": "ECONOMIC_VALUE_MISMATCH", "limit": 500},
    ).json()
    return next(row["break_id"] for row in breaks if row["trade_id"] == TARGET_TRADE_ID)


def test_remediation_endpoints_require_an_api_key(client: Any, target_break_id: str) -> None:
    response = client.post("/remediation/cases", json={"break_id": target_break_id})
    assert response.status_code == 401


def test_generating_a_case_for_an_unsupported_break_family_is_rejected(client: Any) -> None:
    breaks = client.get(
        "/breaks",
        headers=_auth(),
        params={"break_family": "MISSING_REQUIRED_SOURCE", "limit": 1},
    ).json()
    assert breaks, "expected at least one unsupported-family break in the demo corpus"
    response = client.post(
        "/remediation/cases", headers=_auth(), json={"break_id": breaks[0]["break_id"]}
    )
    assert response.status_code == 422


def test_generating_a_case_for_an_unknown_break_is_404(client: Any) -> None:
    response = client.post(
        "/remediation/cases", headers=_auth(), json={"break_id": "break_does_not_exist"}
    )
    assert response.status_code == 404


def test_unknown_case_returns_404_on_every_remediation_endpoint(client: Any) -> None:
    for method, path, body in (
        ("get", "/remediation/cases/case_does_not_exist/evidence", None),
        (
            "post",
            "/remediation/cases/case_does_not_exist/maker-approval",
            {"approver_identity": "someone"},
        ),
        (
            "post",
            "/remediation/cases/case_does_not_exist/checker-approval",
            {"approver_identity": "someone"},
        ),
        ("post", "/remediation/cases/case_does_not_exist/execute", None),
    ):
        kwargs: dict[str, Any] = {"headers": _auth()}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"


@pytest.fixture()
def new_case(client: Any, target_break_id: str) -> dict[str, Any]:
    response = client.post(
        "/remediation/cases", headers=_auth(), json={"break_id": target_break_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_generated_case_is_eligible_with_a_deterministic_recommendation(
    new_case: dict[str, Any],
) -> None:
    assert new_case["policy_decision"]["outcome"] == "ELIGIBLE_FOR_APPROVAL"
    assert new_case["recommendation"]["recommended_action"] == "CORRECT_LEGACY_BOOKING_FIELD"
    assert new_case["recommendation"]["proposed_fields"] == {
        "/payload/base_amount": AUTHORITATIVE_VALUE
    }
    assert new_case["recommendation"]["citations"]
    assessment = new_case["priority_assessment"]
    assert assessment["provider"] == "lightgbm"
    assert assessment["model_version"] == "priority-lgbm-1.0.0"
    assert 0.0 <= assessment["score"] <= 1.0
    assert assessment["priority"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert assessment["shap_contributions"]
    assert assessment["shap_additivity_error"] <= 0.000001


# ---------------------------------------------------------------------
# 6. single approval insufficient
# ---------------------------------------------------------------------
def test_execute_without_any_approval_is_rejected(client: Any, new_case: dict[str, Any]) -> None:
    response = client.post(f"/remediation/cases/{new_case['case_id']}/execute", headers=_auth())
    assert response.status_code == 409


def test_execute_with_only_maker_approval_is_rejected(
    client: Any, new_case: dict[str, Any]
) -> None:
    case_id = new_case["case_id"]
    maker = client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    assert maker.status_code == 200, maker.text

    response = client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())
    assert response.status_code == 409


# ---------------------------------------------------------------------
# 5. Maker and Checker must be different identities
# ---------------------------------------------------------------------
def test_checker_approval_with_the_makers_identity_is_rejected(
    client: Any, new_case: dict[str, Any]
) -> None:
    case_id = new_case["case_id"]
    client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "same.person"},
    )
    response = client.post(
        f"/remediation/cases/{case_id}/checker-approval",
        headers=_auth(),
        json={"approver_identity": "same.person"},
    )
    assert response.status_code == 409

    view = client.get(f"/remediation/cases/{case_id}/evidence", headers=_auth()).json()
    assert len(view["approvals"]) == 1, "the rejected checker approval must not be persisted"


def test_submitting_the_same_role_twice_is_rejected(client: Any, new_case: dict[str, Any]) -> None:
    case_id = new_case["case_id"]
    first = client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.someone-else"},
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------
# 8. unapproved field rejected (executor layer): a hand-built envelope
# proposing a field outside ALLOWED_PROPOSED_FIELDS, which the live API can
# never itself construct -- it only ever builds envelopes from a case's own
# approved_field_path.
# ---------------------------------------------------------------------
def test_executor_rejects_a_field_outside_the_approved_allow_list(
    adapter: Any, client: Any, new_case: dict[str, Any]
) -> None:
    from packages.remediation.envelope import build_envelope
    from packages.remediation.executor import RemediationExecutor
    from packages.remediation.legacy_adapter import MockLegacyBookingAdapter
    from packages.remediation.store import RemediationStore

    case_id = new_case["case_id"]
    client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    client.post(
        f"/remediation/cases/{case_id}/checker-approval",
        headers=_auth(),
        json={"approver_identity": "checker.bob"},
    )

    store = RemediationStore(adapter)
    executor = RemediationExecutor(store, MockLegacyBookingAdapter(store), secret=SIGNING_SECRET)
    case_row = store.get_case(case_id)
    envelope = build_envelope(
        case_id=case_id,
        trade_id=case_row["trade_id"],
        tenant_id=case_row["tenant_id"],
        portfolio_id=case_row["portfolio_id"],
        field_path="/payload/quoted_rate",  # not on ALLOWED_PROPOSED_FIELDS
        approved_value="1.0850",
        expected_old_value=case_row["break_facts"]["observed_value"],
        maker_identity="maker.alice",
        checker_identity="checker.bob",
        idempotency_key=f"idem_{case_id}_unapproved_field",
        secret=SIGNING_SECRET,
    )
    result = executor.execute(envelope, _approvals_from_store(store, case_id))
    assert result.outcome == "REJECTED_FIELD_NOT_ALLOWED"
    assert result.applied is False


# ---------------------------------------------------------------------
# 9. expected-old-value mismatch rejected
# ---------------------------------------------------------------------
def test_executor_rejects_an_envelope_whose_expected_old_value_no_longer_matches(
    adapter: Any, client: Any, new_case: dict[str, Any]
) -> None:
    from packages.remediation.envelope import build_envelope
    from packages.remediation.executor import RemediationExecutor
    from packages.remediation.legacy_adapter import MockLegacyBookingAdapter
    from packages.remediation.store import RemediationStore

    case_id = new_case["case_id"]
    client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    client.post(
        f"/remediation/cases/{case_id}/checker-approval",
        headers=_auth(),
        json={"approver_identity": "checker.bob"},
    )

    store = RemediationStore(adapter)
    executor = RemediationExecutor(store, MockLegacyBookingAdapter(store), secret=SIGNING_SECRET)
    case_row = store.get_case(case_id)
    envelope = build_envelope(
        case_id=case_id,
        trade_id=case_row["trade_id"],
        tenant_id=case_row["tenant_id"],
        portfolio_id=case_row["portfolio_id"],
        field_path="/payload/base_amount",
        approved_value=AUTHORITATIVE_VALUE,
        expected_old_value="0.01",  # deliberately stale/wrong current value
        maker_identity="maker.alice",
        checker_identity="checker.bob",
        idempotency_key=f"idem_{case_id}_stale_expected",
        secret=SIGNING_SECRET,
    )
    result = executor.execute(envelope, _approvals_from_store(store, case_id))
    assert result.outcome == "REJECTED_VALUE_MISMATCH"
    assert result.applied is False
    assert result.read_back_value == LEGACY_VALUE


# ---------------------------------------------------------------------
# 7 (integration companion to tests/test_remediation.py::TestEnvelopeIntegrity):
# an expired envelope is rejected end-to-end through the real executor and
# store, not only at the standalone envelope-verification layer.
# ---------------------------------------------------------------------
def test_executor_rejects_an_expired_envelope(
    adapter: Any, client: Any, new_case: dict[str, Any]
) -> None:
    from packages.remediation.envelope import build_envelope
    from packages.remediation.executor import RemediationExecutor
    from packages.remediation.legacy_adapter import MockLegacyBookingAdapter
    from packages.remediation.store import RemediationStore

    case_id = new_case["case_id"]
    client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    client.post(
        f"/remediation/cases/{case_id}/checker-approval",
        headers=_auth(),
        json={"approver_identity": "checker.bob"},
    )

    store = RemediationStore(adapter)
    executor = RemediationExecutor(store, MockLegacyBookingAdapter(store), secret=SIGNING_SECRET)
    case_row = store.get_case(case_id)
    envelope = build_envelope(
        case_id=case_id,
        trade_id=case_row["trade_id"],
        tenant_id=case_row["tenant_id"],
        portfolio_id=case_row["portfolio_id"],
        field_path="/payload/base_amount",
        approved_value=AUTHORITATIVE_VALUE,
        expected_old_value=case_row["break_facts"]["observed_value"],
        maker_identity="maker.alice",
        checker_identity="checker.bob",
        idempotency_key=f"idem_{case_id}_expired",
        ttl_seconds=-1,  # already expired the instant it is issued
        secret=SIGNING_SECRET,
    )
    result = executor.execute(envelope, _approvals_from_store(store, case_id))
    assert result.outcome == "REJECTED_EXPIRED"
    assert result.applied is False


@pytest.fixture(scope="module")
def approved_case(client: Any, target_break_id: str) -> dict[str, Any]:
    """One case, approved once, shared by the success-narrative tests below
    (10-14), which run in file order: the first execute call is the genuine
    first successful correction; every later call against this same case in
    this module is a real replay/recovery of that one execution, which is
    exactly the scenario those tests need -- a fresh case per test would
    instead see the trade already corrected by an earlier test and fail
    closed on a stale expected_old_value, which is correct executor
    behaviour but not what tests 11-14 are trying to exercise.
    """

    created = client.post("/remediation/cases", headers=_auth(), json={"break_id": target_break_id})
    assert created.status_code == 201, created.text
    case = created.json()
    case_id = case["case_id"]

    maker = client.post(
        f"/remediation/cases/{case_id}/maker-approval",
        headers=_auth(),
        json={"approver_identity": "maker.alice"},
    )
    assert maker.status_code == 200, maker.text
    checker = client.post(
        f"/remediation/cases/{case_id}/checker-approval",
        headers=_auth(),
        json={"approver_identity": "checker.bob"},
    )
    assert checker.status_code == 200, checker.text
    return case


# ---------------------------------------------------------------------
# 10. first approved execution succeeds
# ---------------------------------------------------------------------
def test_first_approved_execution_succeeds(client: Any, approved_case: dict[str, Any]) -> None:
    case_id = approved_case["case_id"]
    response = client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())
    assert response.status_code == 200, response.text
    result = response.json()["execution_result"]
    assert result["outcome"] == "SUCCESS"
    assert result["applied"] is True
    assert result["read_back_value"] == AUTHORITATIVE_VALUE


# ---------------------------------------------------------------------
# 11. replay creates no second side effect
#
# Runs after test_first_approved_execution_succeeds in file order, against
# the same module-scoped approved_case -- so this call is itself the replay
# (a fresh case here would just repeat item 10, not test a replay at all).
# ---------------------------------------------------------------------
def test_replaying_execute_creates_no_second_side_effect(
    client: Any, approved_case: dict[str, Any]
) -> None:
    case_id = approved_case["case_id"]
    replay = client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["execution_result"]["outcome"] == "DUPLICATE_NOOP"
    assert body["execution_result"]["applied"] is False
    assert body["execution_result"]["read_back_value"] == AUTHORITATIVE_VALUE

    executions = body["case"]["executions"]
    assert len(executions) == 2
    assert sum(1 for item in executions if item["applied"]) == 1
    # A replay must not re-run post-action verification: still version 2.
    assert body["case"]["post_action_reconciliation"]["canonical_state_version"] == 2


# ---------------------------------------------------------------------
# 12. timeout-after-save resolved via read-back, not a blind retry
# ---------------------------------------------------------------------
def test_timeout_recovery_reads_back_rather_than_blindly_retrying(
    client: Any, adapter: Any, approved_case: dict[str, Any]
) -> None:
    from packages.remediation.executor import RemediationExecutor
    from packages.remediation.legacy_adapter import MockLegacyBookingAdapter
    from packages.remediation.models import ActionEnvelope
    from packages.remediation.store import RemediationStore

    case_id = approved_case["case_id"]

    # Simulate the caller losing the response to a write that actually
    # succeeded (e.g. a network timeout) and re-attempting with the same
    # signed envelope, as a genuine "did that go through?" recovery -- not a
    # plain replay and not a blind resubmission. The real correction already
    # happened in test_first_approved_execution_succeeds, above.
    store = RemediationStore(adapter)
    executor = RemediationExecutor(store, MockLegacyBookingAdapter(store), secret=SIGNING_SECRET)
    envelope_row = store.get_envelope(case_id)
    envelope = ActionEnvelope.model_validate(envelope_row["envelope_document"])
    recovery = executor.execute(
        envelope, _approvals_from_store(store, case_id), attempt_context="TIMEOUT_RECOVERY_ATTEMPT"
    )
    assert recovery.outcome == "TIMEOUT_RECOVERED"
    assert recovery.applied is False
    assert recovery.read_back_value == AUTHORITATIVE_VALUE

    executions = store.get_executions(case_id)
    assert sum(1 for row in executions if row["applied"]) == 1


# ---------------------------------------------------------------------
# 13. post-action reconciliation marks the break resolved
# ---------------------------------------------------------------------
def test_post_action_reconciliation_marks_the_break_resolved(
    client: Any, approved_case: dict[str, Any]
) -> None:
    case_id = approved_case["case_id"]
    response = client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())
    post_action = response.json()["case"]["post_action_reconciliation"]
    assert post_action["result"] == "PASS"
    assert post_action["break_families"] == []
    assert post_action["trade_id"] == TARGET_TRADE_ID

    # And it holds up under the product's own full-corpus batch pipeline too,
    # not only the scoped rerun -- see the supersession filter in
    # apps/api/service.py::_reconcile_lineage_group.
    time.sleep(1.1)  # run_id is second-granularity; force a distinct run_id
    full_run = client.post("/reconciliation/run", headers=_auth())
    assert full_run.status_code == 200, full_run.text
    latest_run_id = full_run.json()["run_id"]
    latest_breaks = client.get(
        "/breaks",
        headers=_auth(),
        params={
            "break_family": "ECONOMIC_VALUE_MISMATCH",
            "run_id": latest_run_id,
            "limit": 500,
        },
    ).json()
    assert all(row["trade_id"] != TARGET_TRADE_ID for row in latest_breaks)


# ---------------------------------------------------------------------
# 14. evidence contains every required stage
# ---------------------------------------------------------------------
def test_evidence_contains_every_required_stage(client: Any, approved_case: dict[str, Any]) -> None:
    case_id = approved_case["case_id"]
    client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())

    response = client.get(f"/remediation/cases/{case_id}/evidence", headers=_auth())
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["break_facts"]["expected_value"] == AUTHORITATIVE_VALUE
    assert body["break_facts"]["observed_value"] == LEGACY_VALUE
    assert body["ml_priority_assessment"]["provider"] == "lightgbm"
    assert body["ml_priority_assessment"]["training_data"] == "SYNTHETIC_ONLY"
    assert body["ml_priority_assessment"]["shap_contributions"]
    assert body["ai_recommendation"]["recommended_action"] == "CORRECT_LEGACY_BOOKING_FIELD"
    assert body["ai_recommendation"]["citations"]
    assert body["ai_recommendation"]["confidence"] > 0
    assert body["policy_decision"]["outcome"] == "ELIGIBLE_FOR_APPROVAL"
    assert {row["role"] for row in body["approvals"]} == {"MAKER", "CHECKER"}
    assert body["envelope"]["content_hash"].startswith("sha256:")
    assert body["executions"] and body["executions"][0]["outcome"] == "SUCCESS"
    assert body["post_action_reconciliation"]["result"] == "PASS"
    assert body["evidence_id"] == f"evidence_{case_id}"
    assert body["evidence_content_hash"].startswith("sha256:")

    # A further replay must not create a second frozen evidence snapshot --
    # remediation_evidence is insert-only and unique per case.
    client.post(f"/remediation/cases/{case_id}/execute", headers=_auth())
    again = client.get(f"/remediation/cases/{case_id}/evidence", headers=_auth()).json()
    assert again["evidence_id"] == body["evidence_id"]
    assert again["evidence_content_hash"] == body["evidence_content_hash"]
