"""TradeOps Sentinel dashboard.

Talks to the FastAPI service only -- it holds no database credentials and
opens no database connection.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("TRADEOPS_API_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("DASHBOARD_TIMEOUT_SECONDS", "120"))

DISCLAIMER = "Synthetic demonstration data only — no real banking or customer data."

st.set_page_config(page_title="TradeOps Sentinel", page_icon="🛡️", layout="wide")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE_URL,
        headers={"X-API-Key": API_KEY},
        timeout=REQUEST_TIMEOUT,
    )


def api_get(path: str, **params: Any) -> Any:
    with _client() as client:
        response = client.get(path, params={k: v for k, v in params.items() if v})
    if response.status_code >= 400:
        st.error(f"API {response.status_code} on GET {path}: {response.text[:300]}")
        return None
    return response.json()


def api_post(path: str) -> Any:
    with _client() as client:
        response = client.post(path)
    if response.status_code >= 400:
        st.error(f"API {response.status_code} on POST {path}: {response.text[:300]}")
        return None
    return response.json()


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
st.title("🛡️ TradeOps Sentinel")
st.caption(
    "Deterministic FX Spot/Forward trade-break reconciliation with full source "
    "provenance. Every break is reproducible from an approved, versioned rule set."
)
st.warning(DISCLAIMER, icon="⚠️")

health = api_get("/health")
ready = api_get("/ready")
status_col, ready_col, api_col = st.columns(3)
status_col.metric("API", "online" if health else "unreachable")
ready_col.metric("Database", "ready" if (ready or {}).get("ready") else "not ready")
api_col.metric("Endpoint", API_BASE_URL.replace("http://", ""))

if ready and not ready.get("ready"):
    st.error(f"Database not ready: {ready.get('detail', 'unknown')}")

# --------------------------------------------------------------------------
# controls
# --------------------------------------------------------------------------
st.divider()
st.subheader("Controls")
load_col, run_col, refresh_col = st.columns(3)

if load_col.button("📥 Load Synthetic Demo Data", use_container_width=True):
    with st.spinner("Loading approved synthetic FX corpus…"):
        result = api_post("/demo/load")
    if result:
        st.success(
            f"Loaded — inserted {result['inserted']}, "
            f"idempotent replays {result['replayed']}, total {result['total']}."
        )

if run_col.button("▶️ Run Reconciliation", type="primary", use_container_width=True):
    with st.spinner("Running deterministic reconciliation…"):
        result = api_post("/reconciliation/run")
    if result:
        st.success(
            f"Run {result['run_id']} — {result['trades_evaluated']} trades, "
            f"{result['break_count']} breaks detected."
        )

if refresh_col.button("🔄 Refresh Results", use_container_width=True):
    st.rerun()

# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
st.divider()
summary = api_get("/summary")
if not summary:
    st.info("Load demo data and run reconciliation to populate the dashboard.")
    st.stop()

st.subheader("Reconciliation summary")

# Every metric below is scoped to exactly one reconciliation run -- the latest
# completed one. Historical runs are never blended into this view (see the
# "Latest reconciliation runs" table for full history), so which run is being
# shown must be unambiguous.
latest_run_id = summary.get("latest_run_id")
if latest_run_id:
    st.info(
        f"Showing the **latest completed run**: `{latest_run_id}` — "
        f"completed at {summary.get('latest_run_completed_at') or '—'}. "
        "Earlier runs remain stored and are never mixed into these totals.",
        icon="🕒",
    )
else:
    st.info("No completed reconciliation run yet — showing zero totals.", icon="🕒")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Observations", f"{summary['total_observations']:,}")
m2.metric("Canonical trades", f"{summary['total_trades']:,}")
m3.metric("Clean trades", f"{summary['clean_trades']:,}")
m4.metric("Broken trades", f"{summary['broken_trades']:,}")
m5.metric("Total breaks", f"{summary['total_breaks']:,}")

fam_col, prod_col = st.columns([2, 1])
with fam_col:
    st.markdown("**Breaks by family**")
    families = summary.get("breaks_by_family") or {}
    if families:
        frame = pd.DataFrame(
            sorted(families.items(), key=lambda kv: kv[1], reverse=True),
            columns=["Break family", "Count"],
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.bar_chart(frame.set_index("Break family"))
    else:
        st.caption("No breaks recorded yet.")

with prod_col:
    st.markdown("**Trades by product**")
    products = summary.get("trades_by_product") or {}
    if products:
        st.dataframe(
            pd.DataFrame(sorted(products.items()), columns=["Product", "Trades"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No canonical trades yet.")
    st.markdown("**Run provenance**")
    st.caption(f"Run: `{summary.get('latest_run_id') or '—'}`")
    st.caption(f"Completed: {summary.get('latest_run_completed_at') or '—'}")
    st.caption(f"Config hash: `{(summary.get('config_hash') or '—')[:32]}…`")

# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
st.divider()
st.subheader("Latest reconciliation runs")
runs = api_get("/runs", limit=10) or []
if runs:
    st.dataframe(
        pd.DataFrame(runs)[
            [
                "run_id",
                "status",
                "trades_evaluated",
                "clean_trades",
                "broken_trades",
                "break_count",
                "config_hash",
                "completed_at",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("No runs recorded yet.")

# --------------------------------------------------------------------------
# breaks
# --------------------------------------------------------------------------
st.divider()
st.subheader("Detected trade breaks")

f1, f2, f3, f4 = st.columns(4)
product_filter = f1.selectbox("Product", ["All", "FX_SPOT", "FX_FORWARD"])
family_filter = f2.selectbox("Break family", ["All", *sorted(summary.get("breaks_by_family", {}))])
state_filter = f3.selectbox("Status", ["All", "OPEN", "ACKNOWLEDGED", "RESOLVED", "REOPENED"])
search = f4.text_input("Search trade / break id", "")

rows = (
    api_get(
        "/breaks",
        product_type=None if product_filter == "All" else product_filter,
        break_family=None if family_filter == "All" else family_filter,
        state=None if state_filter == "All" else state_filter,
        limit=2000,
    )
    or []
)
if search:
    needle = search.strip().lower()
    rows = [r for r in rows if needle in r["trade_id"].lower() or needle in r["break_id"].lower()]

st.caption(f"{len(rows)} break(s) matching the current filters.")
if rows:
    st.dataframe(
        pd.DataFrame(rows)[
            [
                "break_id",
                "trade_id",
                "product_type",
                "break_family",
                "severity",
                "state",
                "detected_at",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    # ------------------------------------------------------------------
    # break detail
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("Break detail")
    selected = st.selectbox("Select a break", [r["break_id"] for r in rows])
    detail = api_get(f"/breaks/{selected}") if selected else None
    if detail:
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Family", detail["break_family"])
        d2.metric("Severity", detail["severity"])
        d3.metric("Product", detail["product_type"])
        d4.metric("Status", detail["state"])

        st.markdown(
            f"**Trade** `{detail['trade_id']}`  ·  **Run** `{detail['run_id']}`  ·  "
            f"**Detected** {detail['detected_at']}  ·  "
            f"**Canonical version** {detail['canonical_state_version']}"
        )
        st.markdown(f"**Condition code** `{detail['condition_code']}`")
        st.markdown(f"**Config hash** `{detail['config_hash']}`")
        st.markdown(
            f"**Detection rule** `{detail.get('detection_rule_version') or '—'}`  ·  "
            f"**Taxonomy** `{detail.get('taxonomy_version') or '—'}`  ·  "
            f"**Priority band** "
            f"`{(detail.get('priority') or {}).get('priority_band', '—')}`"
        )

        st.markdown("### Expected vs observed")
        comparisons = detail.get("comparisons") or []
        if comparisons:
            st.dataframe(pd.json_normalize(comparisons), use_container_width=True, hide_index=True)
        else:
            st.caption("This family reports evidence rather than field comparisons.")

        st.markdown("### Source provenance (source version set)")
        svs = detail.get("source_version_set") or []
        if svs:
            st.dataframe(pd.json_normalize(svs), use_container_width=True, hide_index=True)

        st.markdown("### Evidence")
        evidence = detail.get("evidence") or []
        if evidence:
            st.dataframe(pd.json_normalize(evidence), use_container_width=True, hide_index=True)
        else:
            st.caption("No additional evidence items recorded for this break.")

        with st.expander("Full typed break document"):
            st.json(detail.get("break_document") or {})

        with st.expander("Canonical trade state and provenance"):
            trade = api_get(f"/trades/{detail['trade_id']}")
            if trade:
                st.markdown(f"**Canonical content hash** `{trade['content_hash']}`")
                st.json(trade["state"])
                st.markdown("**Field provenance**")
                st.json(trade["field_provenance"])
else:
    st.caption("No breaks match the current filters.")

st.divider()
st.caption(DISCLAIMER)
