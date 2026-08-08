"""Real PostgreSQL end-to-end test for the attended UiPath browser boundary."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

psycopg = pytest.importorskip("psycopg")
fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

DATABASE_URL = os.getenv("TRADEOPS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="TRADEOPS_TEST_DATABASE_URL is required for UiPath integration tests",
)

API_KEY = "test-api-key-not-a-real-secret"
SIGNING_SECRET = "test-remediation-signing-secret-not-a-real-secret"
TARGET_TRADE_ID = "trade_20118124703fa8bb1c8ccc5c8c035285"
AUTHORITATIVE_VALUE = "1018000.00"


@pytest.fixture(scope="module")
def api_env() -> Iterator[None]:
    keys = (
        "DATABASE_URL",
        "TRADEOPS_API_KEY",
        "TRADEOPS_REMEDIATION_SIGNING_SECRET",
        "TRADEOPS_UIPATH_BASE_URL",
    )
    previous = {key: os.environ.get(key) for key in keys}
    assert DATABASE_URL is not None
    os.environ.update(
        {
            "DATABASE_URL": DATABASE_URL,
            "TRADEOPS_API_KEY": API_KEY,
            "TRADEOPS_REMEDIATION_SIGNING_SECRET": SIGNING_SECRET,
            "TRADEOPS_UIPATH_BASE_URL": "http://127.0.0.1:8000",
        }
    )
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="module")
def client(api_env: None) -> Iterator[Any]:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.commit()

    from apps.api.main import app

    with TestClient(app) as test_client:
        yield test_client


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_attended_uipath_screen_executes_only_an_approved_signed_action(client: Any) -> None:
    assert client.post("/demo/load", headers=_auth()).status_code == 200
    assert client.post("/reconciliation/run", headers=_auth()).status_code == 200
    breaks = client.get(
        "/breaks",
        headers=_auth(),
        params={"break_family": "ECONOMIC_VALUE_MISMATCH", "limit": 500},
    ).json()
    break_id = next(row["break_id"] for row in breaks if row["trade_id"] == TARGET_TRADE_ID)
    case = client.post("/remediation/cases", headers=_auth(), json={"break_id": break_id}).json()
    case_id = case["case_id"]

    denied = client.post(f"/remediation/cases/{case_id}/uipath/prepare", headers=_auth())
    assert denied.status_code == 409

    for role, identity in (("maker", "maker.alice"), ("checker", "checker.bob")):
        approved = client.post(
            f"/remediation/cases/{case_id}/{role}-approval",
            headers=_auth(),
            json={"approver_identity": identity},
        )
        assert approved.status_code == 200, approved.text

    prepared = client.post(f"/remediation/cases/{case_id}/uipath/prepare", headers=_auth())
    assert prepared.status_code == 200, prepared.text
    launch = prepared.json()
    assert launch["execution_mode"] == "ATTENDED_COMMUNITY"
    parsed = urlsplit(launch["launch_url"])
    token = parse_qs(parsed.query)["token"][0]

    assert (
        client.get(parsed.path, params={"token": "wrong-token-value-long-enough"}).status_code
        == 404
    )
    screen = client.get(parsed.path, params={"token": token})
    assert screen.status_code == 200, screen.text
    assert "READY FOR ATTENDED RUN" in screen.text
    assert TARGET_TRADE_ID in screen.text
    assert "1019000.00" in screen.text
    assert AUTHORITATIVE_VALUE in screen.text

    apply_path = f"{parsed.path}/apply"
    query = {"token": token, "robot_reference": "uipath-studio-web-attended-test"}
    first = client.post(apply_path, params=query)
    assert first.status_code == 200, first.text
    first_html = "".join(first.text.split())
    assert 'data-testid="execution-outcome">SUCCESS<' in first_html
    assert f'data-testid="read-back-value">{AUTHORITATIVE_VALUE}<' in first_html

    replay = client.post(apply_path, params=query)
    assert replay.status_code == 200, replay.text
    assert 'data-testid="execution-outcome">DUPLICATE_NOOP<' in "".join(replay.text.split())

    evidence = client.get(f"/remediation/cases/{case_id}/evidence", headers=_auth()).json()
    events = evidence["uipath_execution_events"]
    assert [event["event_type"] for event in events] == [
        "PREPARED",
        "STARTED",
        "COMPLETED",
        "STARTED",
        "COMPLETED",
    ]
    completed = [event for event in events if event["event_type"] == "COMPLETED"]
    assert [event["outcome"] for event in completed] == ["SUCCESS", "DUPLICATE_NOOP"]
    assert sum(1 for event in completed if event["applied"]) == 1
    assert all("token" not in event for event in events)
    assert evidence["post_action_reconciliation"]["result"] == "PASS"
