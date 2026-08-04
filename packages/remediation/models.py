"""Typed contracts for the controlled-AI remediation slice.

Mirrors the strict-by-default style of ``packages.contracts.models``: every
model forbids unknown fields and strips whitespace, so a malformed AI
response or a malformed API request fails validation immediately rather than
silently passing through with extra/garbled fields.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RemediationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


RiskTier = Literal["LOW", "MEDIUM", "HIGH"]
Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ApprovalRole = Literal["MAKER", "CHECKER"]
ApprovalDecision = Literal["APPROVE", "REJECT"]

# Closed action allow-list for this slice. Anything else -- order submission,
# cancellation, amendment, a price/rate decision, a live trading action -- is
# never a valid ``recommended_action`` value, so it cannot even be expressed,
# let alone approved or executed.
RecommendedAction = Literal["CORRECT_LEGACY_BOOKING_FIELD", "MANUAL_INVESTIGATION"]

# The only field this slice is allowed to touch. Anything else, however the
# AI phrases it, is rejected by the policy engine.
ALLOWED_PROPOSED_FIELDS: frozenset[str] = frozenset({"/payload/base_amount"})

# Actions that must always be rejected even if an LLM (or a hand-crafted API
# call bypassing the LLM) proposes one of them. Checked independently of the
# RecommendedAction Literal, which already can't express these values -- this
# is a second, explicit gate against the raw string in case a caller sends
# unvalidated JSON directly against a lower-level function.
PROHIBITED_ACTIONS: frozenset[str] = frozenset(
    {
        "SUBMIT_ORDER",
        "CANCEL_ORDER",
        "AMEND_ORDER",
        "PRICE_DECISION",
        "TRADING_ACTION",
    }
)


class BreakFacts(RemediationModel):
    """The only input the AI triage component receives.

    Deliberately just the structured reconciliation facts -- no raw source
    documents, no database access, no ability to reference anything the
    deterministic engine didn't already compute and type-check.
    """

    break_id: str
    break_family: str
    condition_code: str
    product_type: str
    trade_id: str
    field_path: str
    expected_value: str
    observed_value: str
    expected_source_system: str
    observed_source_system: str
    trade_value_amount: str
    trade_value_currency: str


class Citation(RemediationModel):
    document_id: str
    section: str

    @property
    def citation_key(self) -> str:
        return f"{self.document_id}#{self.section}"


class AIRecommendation(RemediationModel):
    """Strict structured output schema every AI provider must satisfy."""

    predicted_root_cause: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    priority: Priority
    recommended_action: RecommendedAction | None
    proposed_fields: dict[str, str] = Field(default_factory=dict)
    risk_tier: RiskTier
    required_approvals: list[ApprovalRole] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    abstain_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _action_and_abstain_are_mutually_exclusive(self) -> AIRecommendation:
        if self.recommended_action is None and not self.abstain_reason:
            raise ValueError("recommended_action is null but abstain_reason is missing")
        if self.recommended_action is not None and self.abstain_reason:
            raise ValueError("recommended_action and abstain_reason are mutually exclusive")
        if self.recommended_action == "CORRECT_LEGACY_BOOKING_FIELD" and not self.proposed_fields:
            raise ValueError("CORRECT_LEGACY_BOOKING_FIELD requires at least one proposed field")
        return self


def recommendation_content_hash(recommendation: AIRecommendation | dict[str, Any]) -> str:
    """Bind an approval to exactly the recommendation a Maker or Checker reviewed.

    Takes either the live model or its persisted ``model_dump(mode="json")``
    dict so a Maker's approval (computed from the just-generated model) and a
    Checker's approval (computed later from the row read back out of
    ``remediation_cases``) produce an identical hash for the same content.
    """

    payload = (
        recommendation.model_dump(mode="json")
        if isinstance(recommendation, AIRecommendation)
        else recommendation
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


PolicyOutcome = Literal["ELIGIBLE_FOR_APPROVAL", "MANUAL_INVESTIGATION", "REJECTED"]


class PolicyDecision(RemediationModel):
    """The deterministic policy engine's evaluation of an AIRecommendation.

    Authoritative over the recommendation's own ``required_approvals`` and
    ``risk_tier`` -- those are the LLM's advisory opinion; this is the
    engine's independently-derived, hardcoded-rule decision.
    """

    outcome: PolicyOutcome
    reasons: list[str]
    required_approvals: list[ApprovalRole]
    approved_field_path: str | None = None
    approved_value: str | None = None


class Approval(RemediationModel):
    role: ApprovalRole
    approver_identity: str = Field(min_length=1, max_length=200)
    decision: ApprovalDecision
    decided_at: datetime
    approved_recommendation_hash: str


class ActionEnvelope(RemediationModel):
    """Deterministic, signed, expiring, idempotent action instruction.

    Every field the executor needs to make its accept/reject decision without
    consulting anything other than the envelope itself and the current state
    of the target record.
    """

    case_id: str
    trade_id: str
    tenant_id: str
    portfolio_id: str
    action_type: Literal["CORRECT_LEGACY_BOOKING_FIELD"]
    field_path: str
    approved_value: str
    expected_old_value: str
    maker_identity: str
    checker_identity: str
    issued_at: datetime
    expires_at: datetime
    idempotency_key: str
    content_hash: str
    signature: str


ExecutionOutcome = Literal[
    "SUCCESS",
    "DUPLICATE_NOOP",
    "TIMEOUT_RECOVERED",
    "REJECTED_EXPIRED",
    "REJECTED_TAMPERED",
    "REJECTED_APPROVALS_MISSING",
    "REJECTED_SAME_IDENTITY",
    "REJECTED_VALUE_MISMATCH",
    "REJECTED_FIELD_NOT_ALLOWED",
]


class ExecutionResult(RemediationModel):
    outcome: ExecutionOutcome
    detail: str
    read_back_value: str | None = None
    applied: bool


__all__ = [
    "ALLOWED_PROPOSED_FIELDS",
    "PROHIBITED_ACTIONS",
    "ActionEnvelope",
    "AIRecommendation",
    "Approval",
    "ApprovalDecision",
    "ApprovalRole",
    "BreakFacts",
    "Citation",
    "ExecutionOutcome",
    "ExecutionResult",
    "Priority",
    "PolicyDecision",
    "PolicyOutcome",
    "RecommendedAction",
    "RemediationModel",
    "RiskTier",
    "recommendation_content_hash",
]
