"""End-to-end product tests: migrations -> demo load -> reconcile -> API.

Requires a disposable PostgreSQL endpoint in ``TRADEOPS_TEST_DATABASE_URL``.
Local unit-only runs skip this module.
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
    reason="TRADEOPS_TEST_DATABASE_URL is required for product end-to-end tests",
)

API_KEY = "test-api-key-not-a-real-secret"

# The engine supports all eight approved families (proven by the unchanged
# TS-11 suite). The MVP pipeline exercises seven: POST_ACTION_VERIFICATION_FAILURE
# requires an executed action with pre/post reads, i.e. the ADR-011 executor,
# which is deliberately out of scope for this product slice.
PIPELINE_FAMILIES = {
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
}
EXECUTOR_ONLY_FAMILY = "POST_ACTION_VERIFICATION_FAILURE"


@pytest.fixture(scope="module")
def api_env() -> Iterator[None]:
    previous = {
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "TRADEOPS_API_KEY": os.environ.get("TRADEOPS_API_KEY"),
    }
    assert DATABASE_URL is not None
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["TRADEOPS_API_KEY"] = API_KEY
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


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# 1. fresh database migrations succeed
def test_migrations_apply_to_a_fresh_database(client: Any) -> None:
    response = client.get("/ready")
    assert response.status_code == 200, response.text
    assert response.json()["ready"] is True


def test_health_needs_no_database_and_no_key(client: Any) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# 13. no successful run yet -> a controlled empty result, never an error.
# Runs before any demo load or reconciliation in this module, on pristine
# migrated-but-empty tables.
def test_no_completed_run_returns_a_controlled_empty_summary_and_break_list(client: Any) -> None:
    summary = client.get("/summary", headers=_auth()).json()
    assert summary["latest_run_id"] is None
    assert summary["latest_run_completed_at"] is None
    assert summary["config_hash"] is None
    assert summary["total_breaks"] == 0
    assert summary["total_trades"] == 0
    assert summary["breaks_by_family"] == {}

    breaks = client.get("/breaks", headers=_auth()).json()
    assert breaks == []


# 8. invalid API key is rejected
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "wrong-key"}],
    ids=["missing", "wrong"],
)
def test_invalid_api_key_is_rejected(client: Any, headers: dict[str, str]) -> None:
    for method, path in (
        ("get", "/summary"),
        ("get", "/breaks"),
        ("post", "/demo/load"),
        ("post", "/reconciliation/run"),
    ):
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
        assert "detail" in response.json()


# 2 + 3. demo data loads, and repeating it is safe/idempotent
def test_demo_load_is_idempotent(client: Any) -> None:
    first = client.post("/demo/load", headers=_auth())
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["inserted"] > 0
    assert first_body["tenant_id"] == "tenant_demo"

    second = client.post("/demo/load", headers=_auth())
    assert second.status_code == 200, second.text
    second_body = second.json()
    # Nothing new is written; every observation is recognised as a replay.
    assert second_body["inserted"] == 0
    assert second_body["replayed"] == first_body["inserted"]
    assert second_body["conflicted"] == first_body["conflicted"]


# 4. reconciliation runs successfully
def test_reconciliation_runs(client: Any) -> None:
    response = client.post("/reconciliation/run", headers=_auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trades_evaluated"] > 0
    assert body["break_count"] > 0
    assert body["clean_trades"] + body["broken_trades"] == body["trades_evaluated"]
    assert body["config_hash"].startswith("sha256:")


# 6. summary totals are internally consistent
def test_summary_totals_are_consistent(client: Any) -> None:
    body = client.get("/summary", headers=_auth()).json()
    assert body["total_observations"] > 0
    assert body["total_trades"] > 0
    assert body["clean_trades"] + body["broken_trades"] == body["total_trades"]
    assert body["total_breaks"] == sum(body["breaks_by_family"].values())
    assert set(body["trades_by_product"]) == {"FX_SPOT", "FX_FORWARD"}
    assert body["latest_run_id"] is not None


# 5. break families supported by the pipeline
def test_pipeline_detects_every_non_executor_break_family(client: Any) -> None:
    families = set(client.get("/summary", headers=_auth()).json()["breaks_by_family"])
    missing = PIPELINE_FAMILIES - families
    assert not missing, f"pipeline failed to surface families: {sorted(missing)}"
    # Stated explicitly rather than silently: this family needs the ADR-011
    # executor (an action instruction plus pre/post reads), which this MVP does
    # not implement. The engine itself still supports it -- see the unchanged
    # TS-11 suite in tests/test_reconciliation.py.
    assert EXECUTOR_ONLY_FAMILY not in families


def test_runs_endpoint_reports_run_provenance(client: Any) -> None:
    runs = client.get("/runs", headers=_auth()).json()
    assert runs
    row = runs[0]
    for field in ("run_id", "config_hash", "status", "trades_evaluated", "completed_at"):
        assert field in row
    assert row["status"] == "COMPLETED"


def test_breaks_can_be_filtered(client: Any) -> None:
    every = client.get("/breaks", headers=_auth(), params={"limit": 2000}).json()
    assert every

    spot = client.get("/breaks", headers=_auth(), params={"product_type": "FX_SPOT"}).json()
    assert spot and all(row["product_type"] == "FX_SPOT" for row in spot)

    family = sorted({row["break_family"] for row in every})[0]
    filtered = client.get("/breaks", headers=_auth(), params={"break_family": family}).json()
    assert filtered and all(row["break_family"] == family for row in filtered)


# 7. break detail includes provenance and evidence
def test_break_detail_includes_provenance_and_evidence(client: Any) -> None:
    rows = client.get("/breaks", headers=_auth(), params={"limit": 2000}).json()
    detail = client.get(f"/breaks/{rows[0]['break_id']}", headers=_auth())
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert body["source_version_set"], "break detail must carry source provenance"
    for item in body["source_version_set"]:
        # TradeBreak provenance uses BreakSourceReference, whose observation key
        # is source_observation_id (not the canonical-state observation_id).
        assert item["source_observation_id"]
        assert item["source_system"]
        assert item["content_hash"].startswith("sha256:")
    assert body["config_hash"].startswith("sha256:")
    assert body["detection_rule_version"]
    assert body["break_document"]["break_id"] == body["break_id"]
    # Expected/observed evidence: a family reports comparisons, evidence, or both.
    assert body["comparisons"] or body["evidence"]


def test_trade_detail_returns_canonical_state_and_related_breaks(client: Any) -> None:
    rows = client.get("/breaks", headers=_auth(), params={"limit": 2000}).json()
    trade_id = rows[0]["trade_id"]
    response = client.get(f"/trades/{trade_id}", headers=_auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trade_id"] == trade_id
    assert body["content_hash"].startswith("sha256:")
    assert body["state"]["product_type"] in {"FX_SPOT", "FX_FORWARD"}
    assert body["field_provenance"]
    assert body["source_version_set"]
    assert all(item["trade_id"] == trade_id for item in body["breaks"])


def test_unknown_break_and_trade_return_404(client: Any) -> None:
    assert client.get("/breaks/break_does_not_exist", headers=_auth()).status_code == 404
    assert client.get("/trades/trade_does_not_exist", headers=_auth()).status_code == 404


# 12. no data outside the requested demo scope
def test_no_data_is_returned_outside_the_demo_scope(client: Any) -> None:
    from apps.api.service import DEMO_PORTFOLIO_IDS, DEMO_TENANT_ID

    summary = client.get("/summary", headers=_auth()).json()
    assert summary["tenant_id"] == DEMO_TENANT_ID
    assert set(summary["portfolio_ids"]) == set(DEMO_PORTFOLIO_IDS)

    # Plant a row in a foreign tenant AND a foreign portfolio, then prove no
    # endpoint surfaces it.
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            INSERT INTO trade_breaks (
                break_id, break_version, run_id, tenant_id, portfolio_id,
                correlation_id, trade_id, canonical_state_version, product_type,
                break_family, condition_code, severity, state, detected_at,
                break_document, source_version_set, config_hash
            ) VALUES (
                'break_foreign_tenant', 1, 'run_foreign', 'tenant_other',
                'portfolio_other', 'corr_other', 'trade_other', 1, 'FX_SPOT',
                'ECONOMIC_VALUE_MISMATCH', 'X', 'HIGH', 'OPEN', now(),
                '{}'::jsonb, '[]'::jsonb, 'sha256:' || repeat('0', 64)
            )
            ON CONFLICT DO NOTHING
            """
        )
        connection.commit()

    rows = client.get("/breaks", headers=_auth(), params={"limit": 2000}).json()
    assert all(row["break_id"] != "break_foreign_tenant" for row in rows)
    assert client.get("/breaks/break_foreign_tenant", headers=_auth()).status_code == 404
    assert client.get("/trades/trade_other", headers=_auth()).status_code == 404


# Regression: reconciliation_runs/trade_breaks are append-only, so a second
# run must add a second historical run and 90 more historical break rows --
# while every default-scoped view (summary, default breaks list, and
# everything the dashboard consumes) keeps showing only the latest run's 90,
# and each historical run remains individually inspectable by its own run_id.
def test_second_reconciliation_run_preserves_history_but_default_view_shows_latest_only(
    client: Any,
) -> None:
    assert DATABASE_URL is not None
    demo_scope_filter = (
        "tenant_id = 'tenant_demo' AND portfolio_id IN ('portfolio_london', 'portfolio_sydney')"
    )

    # Baseline: exactly one run has completed so far in this module (from
    # test_reconciliation_runs). Scoped to the demo tenant/portfolios so the
    # foreign-tenant marker row planted by
    # test_no_data_is_returned_outside_the_demo_scope doesn't skew the count.
    first_run_id = client.get("/summary", headers=_auth()).json()["latest_run_id"]
    assert first_run_id is not None
    with psycopg.connect(DATABASE_URL) as connection:
        historical_before = connection.execute(
            f"SELECT count(*) FROM trade_breaks WHERE {demo_scope_filter}"  # noqa: S608
        ).fetchone()[0]
        runs_before = connection.execute(
            f"SELECT count(*) FROM reconciliation_runs WHERE {demo_scope_filter}"  # noqa: S608
        ).fetchone()[0]
    assert historical_before == 90
    assert runs_before == 2  # one reconciliation_runs row per portfolio

    # 4. Second run, at least a second later so completed_at strictly orders
    # the two runs. (Selection is still correct regardless -- row_id is the
    # deterministic tiebreaker -- this sleep just matches the specified
    # reproduction scenario.)
    time.sleep(1.1)
    second = client.post("/reconciliation/run", headers=_auth())
    assert second.status_code == 200, second.text
    second_run_id = second.json()["run_id"]
    assert second_run_id != first_run_id

    # History: append-only. Nothing collapsed, overwritten, or deduplicated.
    with psycopg.connect(DATABASE_URL) as connection:
        historical_after = connection.execute(
            f"SELECT count(*) FROM trade_breaks WHERE {demo_scope_filter}"  # noqa: S608
        ).fetchone()[0]
        runs_after = connection.execute(
            f"SELECT count(*) FROM reconciliation_runs WHERE {demo_scope_filter}"  # noqa: S608
        ).fetchone()[0]
    assert historical_after == 180  # 5. both runs' 90 breaks each, all still stored
    assert runs_after == 4  # two portfolios x two runs, every row preserved

    # 6 + 7. Default summary/breaks report only the latest completed run.
    summary = client.get("/summary", headers=_auth()).json()
    assert summary["latest_run_id"] == second_run_id
    assert summary["total_trades"] == 144
    assert summary["clean_trades"] == 54
    assert summary["broken_trades"] == 90
    assert summary["total_breaks"] == 90
    assert sum(summary["breaks_by_family"].values()) == 90
    assert summary["trades_by_product"] == {"FX_SPOT": 72, "FX_FORWARD": 72}

    default_breaks = client.get("/breaks", headers=_auth(), params={"limit": 500}).json()
    assert len(default_breaks) == 90
    assert all(row["run_id"] == second_run_id for row in default_breaks)
    # 8. this is exactly what the dashboard renders: it calls /summary and the
    # default /breaks with no run_id, so its visible count is this 90 -- the
    # same number asserted above, not the 180 rows now sitting in history.

    # 10. GET /runs returns both runs' full history.
    runs = client.get("/runs", headers=_auth(), params={"limit": 20}).json()
    demo_runs = [row for row in runs if row["run_id"] in {first_run_id, second_run_id}]
    assert {row["run_id"] for row in demo_runs} == {first_run_id, second_run_id}
    assert len(demo_runs) == 4  # two portfolio rows per run

    # 9. each explicit historical run_id still returns exactly its own 90,
    # and the two sets are disjoint -- proving no cross-run bleed either way.
    first_breaks = client.get(
        "/breaks", headers=_auth(), params={"run_id": first_run_id, "limit": 500}
    ).json()
    second_breaks = client.get(
        "/breaks", headers=_auth(), params={"run_id": second_run_id, "limit": 500}
    ).json()
    assert len(first_breaks) == 90
    assert len(second_breaks) == 90
    first_ids = {row["break_id"] for row in first_breaks}
    second_ids = {row["break_id"] for row in second_breaks}
    assert first_ids.isdisjoint(second_ids)

    # 12. no cross-tenant/portfolio data appears, even with two runs' worth of
    # history now present (reusing the foreign marker row planted earlier by
    # test_no_data_is_returned_outside_the_demo_scope).
    all_returned_ids = first_ids | second_ids | {row["break_id"] for row in default_breaks}
    assert "break_foreign_tenant" not in all_returned_ids

    # 11. repeated demo load remains idempotent with two runs of history present.
    reload = client.post("/demo/load", headers=_auth())
    assert reload.status_code == 200, reload.text
    assert reload.json()["inserted"] == 0


# 9. database unavailable returns a controlled response, never a stack trace
def test_database_unavailable_is_a_controlled_response(api_env: None) -> None:
    from apps.api.main import app

    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = "postgresql://nobody:nobody@127.0.0.1:1/absent?connect_timeout=1"
    try:
        with TestClient(app, raise_server_exceptions=False) as unavailable:
            ready = unavailable.get("/ready")
            assert ready.status_code == 503
            assert ready.json()["ready"] is False

            summary = unavailable.get("/summary", headers=_auth())
            assert summary.status_code == 503
            body = summary.text.lower()
            for leaked in ("traceback", "password", "nobody", "psycopg."):
                assert leaked not in body, f"error response leaked {leaked!r}"
    finally:
        os.environ["DATABASE_URL"] = previous
