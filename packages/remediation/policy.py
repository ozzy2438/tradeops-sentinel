"""Deterministic policy engine.

Evaluates an ``AIRecommendation`` against hardcoded rules and returns a
``PolicyDecision``. This is the only component whose decision is authoritative
-- the AI's own ``required_approvals``, ``risk_tier``, and confidence are
advisory input, never trusted directly. Every rejection path here is a fail
*closed* path: the default for anything unexpected, missing, or ambiguous is
to reject or route to manual investigation, never to proceed.
"""

from __future__ import annotations

from . import retrieval
from .models import (
    ALLOWED_PROPOSED_FIELDS,
    PROHIBITED_ACTIONS,
    AIRecommendation,
    ApprovalRole,
    BreakFacts,
    PolicyDecision,
)

# Below this, a recommendation is never eligible for automated remediation --
# it is routed to manual investigation regardless of what it recommends.
CONFIDENCE_THRESHOLD = 0.7

_SUPPORTED_ACTIONS: frozenset[str] = frozenset({"CORRECT_LEGACY_BOOKING_FIELD"})


def _reject(reason: str) -> PolicyDecision:
    return PolicyDecision(outcome="REJECTED", reasons=[reason], required_approvals=[])


def _manual_investigation(reason: str) -> PolicyDecision:
    return PolicyDecision(outcome="MANUAL_INVESTIGATION", reasons=[reason], required_approvals=[])


def evaluate(recommendation: AIRecommendation, facts: BreakFacts) -> PolicyDecision:
    """Evaluate one recommendation against the fixed rule set for this slice."""

    # Low confidence: manual investigation, full stop -- checked before
    # anything else, so a low-confidence recommendation can never reach an
    # approval flow no matter what else it contains.
    if recommendation.confidence < CONFIDENCE_THRESHOLD:
        return _manual_investigation("confidence_below_threshold")

    if recommendation.recommended_action is None:
        return _manual_investigation(recommendation.abstain_reason or "ai_abstained")

    action = recommendation.recommended_action
    if action in PROHIBITED_ACTIONS or action not in _SUPPORTED_ACTIONS:
        return _reject("action_outside_allow_list")

    # Citation required, and it must actually support this action -- a
    # citation that resolves to a real document/section but wasn't on the
    # closed support-list for this action is treated identically to a
    # missing one (RB-002 §5: "identically to an invalid one").
    if not recommendation.citations:
        return _reject("missing_citation")
    if not any(
        retrieval.citation_supports_action(citation.citation_key, action)
        for citation in recommendation.citations
    ):
        return _reject("citation_does_not_support_action")

    # Exactly one proposed field, on the allow-list, matching the break's own
    # field, with a value that exactly equals the break's own deterministically
    # computed authoritative value -- the AI's restated number is never trusted;
    # it must reproduce what the reconciliation engine already established.
    if len(recommendation.proposed_fields) != 1:
        return _reject("proposed_fields_must_be_exactly_one")
    ((field_path, proposed_value),) = recommendation.proposed_fields.items()
    if field_path not in ALLOWED_PROPOSED_FIELDS:
        return _reject("field_outside_allow_list")
    if field_path != facts.field_path:
        return _reject("field_does_not_match_break")
    if proposed_value != facts.expected_value:
        return _reject("proposed_value_does_not_match_authoritative_source")

    # Economic field correction: Maker + Checker, hardcoded -- independent of
    # whatever the recommendation itself claimed in required_approvals.
    required_approvals: list[ApprovalRole] = ["MAKER", "CHECKER"]

    return PolicyDecision(
        outcome="ELIGIBLE_FOR_APPROVAL",
        reasons=["economic_field_correction_eligible"],
        required_approvals=required_approvals,
        approved_field_path=field_path,
        approved_value=proposed_value,
    )


__all__ = ["CONFIDENCE_THRESHOLD", "evaluate"]
