"""Case view aggregation and the frozen evidence record.

Two related but distinct things:

* ``case_view`` -- a dynamic aggregation of a case's current state (recommendation,
  policy decision, approvals, envelope hash, execution attempts, and the frozen
  evidence if one exists yet). Safe and cheap to call at any stage; this is
  what "read evidence" returns even before a case is finalised.
* ``finalize_evidence`` -- writes the ONE frozen, hashed evidence snapshot for
  a case, once it reaches a terminal state (resolved, or terminally rejected).
  Insert-only, matching the append-only guard on ``remediation_evidence``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .store import RemediationStore


def case_view(store: RemediationStore, case_id: str) -> dict[str, Any] | None:
    """Aggregate a case's full current state. Returns None if unknown."""

    case = store.get_case(case_id)
    if case is None:
        return None
    approvals = store.get_approvals(case_id)
    envelope_row = store.get_envelope(case_id)
    executions = store.get_executions(case_id)
    uipath_events = store.get_uipath_events(case_id)
    evidence_row = store.get_evidence(case_id)

    return {
        "case_id": case["case_id"],
        "break_id": case["break_id"],
        "run_id": case["run_id"],
        "trade_id": case["trade_id"],
        "tenant_id": case["tenant_id"],
        "portfolio_id": case["portfolio_id"],
        "product_type": case["product_type"],
        "ai_provider": case["ai_provider"],
        "break_facts": case["break_facts"],
        "ml_priority_assessment": case["ml_priority_assessment"],
        "ai_recommendation": case["ai_recommendation"],
        "policy_decision": case["policy_decision"],
        "created_at": case["created_at"],
        "approvals": [
            {
                "role": row["role"],
                "approver_identity": row["approver_identity"],
                "decision": row["decision"],
                "decided_at": row["decided_at"],
            }
            for row in approvals
        ],
        "envelope": (
            {
                "content_hash": envelope_row["content_hash"],
                "issued_at": envelope_row["issued_at"],
                "expires_at": envelope_row["expires_at"],
                "idempotency_key": envelope_row["idempotency_key"],
            }
            if envelope_row
            else None
        ),
        "executions": [
            {
                "outcome": row["outcome"],
                "detail": row["detail"],
                "read_back_value": row["read_back_value"],
                "applied": row["applied"],
                "attempted_at": row["attempted_at"],
            }
            for row in executions
        ],
        "uipath_execution_events": [
            {
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "expires_at": row["expires_at"],
                "project_name": row["project_name"],
                "execution_mode": row["execution_mode"],
                "robot_reference": row["robot_reference"],
                "outcome": row["outcome"],
                "detail": row["detail"],
                "read_back_value": row["read_back_value"],
                "applied": row["applied"],
                "occurred_at": row["occurred_at"],
            }
            for row in uipath_events
        ],
        "evidence_id": evidence_row["evidence_id"] if evidence_row else None,
        "evidence_content_hash": evidence_row["content_hash"] if evidence_row else None,
        "post_action_reconciliation": (
            evidence_row["evidence_document"].get("post_action_reconciliation")
            if evidence_row
            else None
        ),
    }


def finalize_evidence(
    store: RemediationStore,
    *,
    case_id: str,
    post_action_reconciliation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Freeze the complete decision and action history for a case.

    Idempotent: if evidence already exists for this case, the existing frozen
    snapshot is returned rather than creating a second one -- the evidence
    table is unique on case_id, matching every other insert-only table in
    this slice.
    """

    view = case_view(store, case_id)
    if view is None:
        raise ValueError(f"cannot finalize evidence for unknown case {case_id!r}")

    document = {
        "case_id": view["case_id"],
        "break_id": view["break_id"],
        "run_id": view["run_id"],
        "trade_id": view["trade_id"],
        "tenant_id": view["tenant_id"],
        "portfolio_id": view["portfolio_id"],
        "product_type": view["product_type"],
        "ai_provider": view["ai_provider"],
        "break_facts": view["break_facts"],
        "ml_priority_assessment": view["ml_priority_assessment"],
        "ai_recommendation": view["ai_recommendation"],
        "policy_decision": view["policy_decision"],
        "approvals": view["approvals"],
        "envelope": view["envelope"],
        "executions": view["executions"],
        "uipath_execution_events": view["uipath_execution_events"],
        "post_action_reconciliation": post_action_reconciliation,
    }
    content_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
    )
    evidence_id = f"evidence_{case_id}"
    return store.insert_evidence_if_absent(
        evidence_id=evidence_id,
        case_id=case_id,
        evidence_document=document,
        content_hash=content_hash,
    )


__all__ = ["case_view", "finalize_evidence"]
