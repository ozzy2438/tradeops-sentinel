#!/usr/bin/env python3
"""Prepare one approved, short-lived UiPath attended demo launch.

Uses only the local TradeOps API. It never calls Azure OpenAI, UiPath Cloud,
or PostgreSQL directly. The raw launch token is printed once in the resulting
JSON and is not written to disk by this script.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TARGET_TRADE_ID = "trade_20118124703fa8bb1c8ccc5c8c035285"


def _request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> Any:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=encoded,
        method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit local operator URL
        return json.load(response)


def main() -> int:
    base_url = os.getenv("TRADEOPS_API_BASE_URL", "http://127.0.0.1:8000")
    api_key = os.getenv("TRADEOPS_API_KEY", "")
    if not api_key:
        print("TRADEOPS_API_KEY is required", file=sys.stderr)
        return 2

    _request(base_url, api_key, "POST", "/demo/load")
    _request(base_url, api_key, "POST", "/reconciliation/run")
    query = urlencode({"break_family": "ECONOMIC_VALUE_MISMATCH", "limit": 500})
    breaks = _request(base_url, api_key, "GET", f"/breaks?{query}")
    target = next((row for row in breaks if row["trade_id"] == TARGET_TRADE_ID), None)
    if target is None:
        print("target synthetic break was not found", file=sys.stderr)
        return 1

    case = _request(
        base_url,
        api_key,
        "POST",
        "/remediation/cases",
        {"break_id": target["break_id"]},
    )
    case_id = case["case_id"]
    _request(
        base_url,
        api_key,
        "POST",
        f"/remediation/cases/{case_id}/maker-approval",
        {"approver_identity": "demo.maker"},
    )
    _request(
        base_url,
        api_key,
        "POST",
        f"/remediation/cases/{case_id}/checker-approval",
        {"approver_identity": "demo.checker"},
    )
    launch = _request(
        base_url,
        api_key,
        "POST",
        f"/remediation/cases/{case_id}/uipath/prepare",
    )
    print(json.dumps(launch, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
