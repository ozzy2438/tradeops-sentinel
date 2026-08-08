"""Case generation: fetch a break, build BreakFacts, retrieve candidate
citations, call the AI provider, evaluate policy, persist the case, and seed
the mock legacy booking record the case's correction (if any) would act on.

Deliberately scoped to exactly the one break family/field this slice
supports -- ``CaseNotEligibleError`` is raised, not silently worked around,
for anything else.
"""

from __future__ import annotations

import secrets
from typing import Any

from packages.persistence.adapter import PostgresAdapter
from packages.priority_model.models import PriorityAssessment, PriorityProvider

from . import policy, retrieval
from .ai_provider import AIProvider
from .models import AIRecommendation, BreakFacts, PolicyDecision
from .store import RemediationStore

SUPPORTED_BREAK_FAMILY = "ECONOMIC_VALUE_MISMATCH"
SUPPORTED_FIELD_PATH = "/payload/base_amount"


class BreakNotFoundError(RuntimeError):
    """The requested break_id does not exist in the demo scope."""


class CaseNotFoundError(RuntimeError):
    """The requested case_id does not exist."""


class CaseNotEligibleError(RuntimeError):
    """The break is not the single supported scenario for this slice."""


def _new_case_id() -> str:
    return f"case_{secrets.token_hex(12)}"


def _decimal_scale(value: str) -> int:
    """Fractional digit count of a decimal string, e.g. '1019000.00' -> 2.

    Amounts in this system always carry exactly ``scale`` fractional digits
    (enforced by ``DecimalAmount.scale_matches_value``), so the string itself
    is a sufficient source for the scale the mock legacy record needs -- no
    raw payload lookup required.
    """

    return len(value.partition(".")[2])


def _build_break_facts(
    break_row: dict[str, Any], canonical_document: dict[str, Any]
) -> tuple[BreakFacts, dict[str, Any]]:
    """Return the strict AI-input facts, plus the raw observed-side source
    reference (needed to seed the mock legacy record, kept separate from the
    facts the AI itself receives).

    ``break_row`` is the row returned by ``PostgresAdapter.break_detail`` --
    a mix of top-level columns (``break_family``, ``source_version_set``, ...)
    and one nested ``break_document`` column holding the full serialised
    ``TradeBreak`` (only place ``comparisons`` actually lives).
    """

    if break_row["break_family"] != SUPPORTED_BREAK_FAMILY:
        raise CaseNotEligibleError(
            f"only {SUPPORTED_BREAK_FAMILY} breaks are supported in this slice, "
            f"got {break_row['break_family']!r}"
        )
    comparisons = break_row["break_document"].get("comparisons") or []
    comparison = next(
        (item for item in comparisons if item["field_path"] == SUPPORTED_FIELD_PATH), None
    )
    if comparison is None:
        raise CaseNotEligibleError(
            f"break has no {SUPPORTED_FIELD_PATH} comparison; unsupported field"
        )
    references = {
        item["source_observation_id"]: item for item in break_row.get("source_version_set") or []
    }
    expected_ref = references.get(comparison.get("expected_source_observation_id"))
    observed_ref = references.get(comparison.get("observed_source_observation_id"))
    if expected_ref is None or observed_ref is None:
        raise CaseNotEligibleError("break comparison is missing its source references")

    base_amount_state = canonical_document["state"].get("base_amount") or {}
    currency = str(base_amount_state.get("currency", ""))

    facts = BreakFacts(
        break_id=str(break_row["break_id"]),
        break_family=str(break_row["break_family"]),
        condition_code=str(break_row["condition_code"]),
        product_type=str(break_row["product_type"]),
        trade_id=str(break_row["trade_id"]),
        field_path=str(comparison["field_path"]),
        expected_value=str(comparison["expected_value"]),
        observed_value=str(comparison["observed_value"]),
        expected_source_system=str(expected_ref["source_system"]),
        observed_source_system=str(observed_ref["source_system"]),
        trade_value_amount=str(comparison["expected_value"]),
        trade_value_currency=currency,
    )
    return facts, observed_ref


def generate_case(
    *,
    break_id: str,
    scope: dict[str, Any],
    product_adapter: PostgresAdapter,
    store: RemediationStore,
    provider: AIProvider,
    priority_provider: PriorityProvider,
) -> dict[str, Any]:
    """Generate one AI recommendation + policy decision for one break.

    Returns the case_id together with the recommendation and policy decision
    that were just persisted, so a caller doesn't need a second round trip.
    """

    break_row = product_adapter.break_detail(**scope, break_id=break_id)
    if break_row is None:
        raise BreakNotFoundError(f"break {break_id!r} not found")

    trade_id = str(break_row["trade_id"])
    canonical_document = product_adapter.canonical_state_document(**scope, trade_id=trade_id)
    if canonical_document is None:
        raise CaseNotEligibleError(f"no canonical state found for trade {trade_id!r}")

    facts, observed_ref = _build_break_facts(break_row, canonical_document)

    candidate_query = (
        f"{facts.break_family} {facts.field_path} economic value mismatch "
        f"{facts.expected_source_system} {facts.observed_source_system} "
        "correction authoritative source booking"
    )
    candidates = retrieval.search(candidate_query, top_k=5)
    recommendation: AIRecommendation = provider.recommend(
        facts=facts, candidate_citations=candidates
    )
    priority_assessment: PriorityAssessment = priority_provider.assess(facts)
    decision: PolicyDecision = policy.evaluate(recommendation, facts)

    case_id = _new_case_id()
    tenant_id = str(scope["tenant_id"])
    portfolio_id = str(canonical_document["portfolio_id"])
    store.insert_case(
        case_id=case_id,
        break_id=facts.break_id,
        run_id=str(break_row["run_id"]),
        trade_id=trade_id,
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        product_type=facts.product_type,
        ai_provider=provider.name,
        break_facts=facts.model_dump(mode="json"),
        ai_recommendation=recommendation.model_dump(mode="json"),
        policy_decision=decision.model_dump(mode="json"),
        ml_priority_assessment=priority_assessment.model_dump(mode="json"),
    )

    # Seed the mock legacy record from the observed (booking) side of the
    # comparison, so the executor has a real current value to check against.
    # Idempotent: re-generating a case for the same trade never clobbers a
    # value a prior remediation may already have corrected.
    store.seed_legacy_booking_record(
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        trade_id=trade_id,
        base_amount_value=facts.observed_value,
        base_amount_currency=facts.trade_value_currency,
        base_amount_scale=_decimal_scale(facts.observed_value),
        source_observation_id=str(observed_ref["source_observation_id"]),
    )

    return {
        "case_id": case_id,
        "recommendation": recommendation,
        "priority_assessment": priority_assessment,
        "decision": decision,
    }


__all__ = [
    "SUPPORTED_BREAK_FAMILY",
    "SUPPORTED_FIELD_PATH",
    "BreakNotFoundError",
    "CaseNotEligibleError",
    "CaseNotFoundError",
    "generate_case",
]
