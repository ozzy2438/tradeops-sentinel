"""Unit tests for the controlled-AI remediation slice's pure logic layer.

Structured AI output schema, deterministic policy fail-closed rules, and the
signed envelope's tamper/expiry detection -- none of this needs a database.
Break detection, approvals, execution, replay/timeout recovery, post-action
reconciliation, and evidence are covered end-to-end in
``tests/integration/test_remediation_e2e.py``, which needs a real PostgreSQL
instance to exercise the actual reconciliation pipeline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from packages.remediation import envelope as envelope_module
from packages.remediation import policy, retrieval
from packages.remediation.ai_provider import DeterministicTestProvider
from packages.remediation.models import (
    AIRecommendation,
    BreakFacts,
    Citation,
    recommendation_content_hash,
)

FACTS = BreakFacts(
    break_id="break_test_001",
    break_family="ECONOMIC_VALUE_MISMATCH",
    condition_code="DECIMAL_OUTSIDE_TOLERANCE",
    product_type="FX_SPOT",
    trade_id="trade_test_001",
    field_path="/payload/base_amount",
    expected_value="1018000.00",
    observed_value="1019000.00",
    expected_source_system="FIX_EXECUTION",
    observed_source_system="MOCK_LEGACY_BOOKING",
    trade_value_amount="1018000.00",
    trade_value_currency="EUR",
)


def _recommendation(**overrides: Any) -> AIRecommendation:
    base: dict[str, Any] = dict(
        predicted_root_cause=(
            "Legacy booking diverges from the authoritative FIX_EXECUTION value "
            "with no evidenced corporate action."
        ),
        confidence=0.93,
        priority="HIGH",
        recommended_action="CORRECT_LEGACY_BOOKING_FIELD",
        proposed_fields={"/payload/base_amount": "1018000.00"},
        risk_tier="MEDIUM",
        required_approvals=["MAKER", "CHECKER"],
        citations=[Citation(document_id="RB-001", section="3")],
        abstain_reason=None,
    )
    base.update(overrides)
    return AIRecommendation(**base)


# --------------------------------------------------------------------------
# 2. structured AI output schema validation
# --------------------------------------------------------------------------
class TestStructuredOutputSchema:
    def test_deterministic_provider_output_satisfies_the_schema(self) -> None:
        provider = DeterministicTestProvider()
        candidates = retrieval.search("base_amount economic value mismatch booking correction")
        recommendation = provider.recommend(facts=FACTS, candidate_citations=candidates)
        assert recommendation.recommended_action == "CORRECT_LEGACY_BOOKING_FIELD"
        assert recommendation.proposed_fields == {"/payload/base_amount": "1018000.00"}
        assert 0.0 <= recommendation.confidence <= 1.0
        assert recommendation.citations
        for citation in recommendation.citations:
            assert retrieval.citation_exists(citation.citation_key)

    def test_unsupported_break_pattern_abstains_with_a_reason(self) -> None:
        unsupported = FACTS.model_copy(update={"observed_source_system": "FPML_CONFIRMATION"})
        provider = DeterministicTestProvider()
        recommendation = provider.recommend(facts=unsupported, candidate_citations=[])
        assert recommendation.recommended_action is None
        assert recommendation.proposed_fields == {}
        assert recommendation.abstain_reason == "unsupported_break_pattern"

    def test_unknown_fields_are_rejected(self) -> None:
        payload = _recommendation().model_dump(mode="json")
        payload["unexpected_field"] = "not part of the schema"
        with pytest.raises(ValidationError):
            AIRecommendation(**payload)

    def test_recommended_action_and_abstain_reason_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValidationError):
            _recommendation(abstain_reason="also set alongside an action")
        with pytest.raises(ValidationError):
            _recommendation(recommended_action=None, abstain_reason=None)

    def test_correction_action_requires_at_least_one_proposed_field(self) -> None:
        with pytest.raises(ValidationError):
            _recommendation(proposed_fields={})

    def test_prohibited_action_values_cannot_even_be_expressed(self) -> None:
        with pytest.raises(ValidationError):
            _recommendation(recommended_action="SUBMIT_ORDER")

    def test_confidence_is_bounded_to_the_unit_interval(self) -> None:
        with pytest.raises(ValidationError):
            _recommendation(confidence=1.5)
        with pytest.raises(ValidationError):
            _recommendation(confidence=-0.1)


# --------------------------------------------------------------------------
# 3. missing-citation fail-closed
# --------------------------------------------------------------------------
def test_policy_rejects_a_recommendation_with_no_citations() -> None:
    decision = policy.evaluate(_recommendation(citations=[]), FACTS)
    assert decision.outcome == "REJECTED"
    assert "missing_citation" in decision.reasons


def test_policy_rejects_a_citation_that_does_not_support_the_action() -> None:
    # RB-003 is the automation-failure/recovery runbook, not the correction
    # procedure -- a real, resolvable citation that still fails closed.
    recommendation = _recommendation(citations=[Citation(document_id="RB-003", section="1")])
    decision = policy.evaluate(recommendation, FACTS)
    assert decision.outcome == "REJECTED"
    assert "citation_does_not_support_action" in decision.reasons


# --------------------------------------------------------------------------
# 4. low-confidence abstain
# --------------------------------------------------------------------------
def test_policy_routes_low_confidence_to_manual_investigation_before_anything_else() -> None:
    # Deliberately paired with an otherwise-perfect recommendation: confidence
    # is checked first, so nothing else can rescue a low-confidence case.
    decision = policy.evaluate(_recommendation(confidence=0.5), FACTS)
    assert decision.outcome == "MANUAL_INVESTIGATION"
    assert decision.reasons == ["confidence_below_threshold"]
    assert decision.required_approvals == []


def test_ai_abstention_routes_to_manual_investigation() -> None:
    recommendation = _recommendation(
        recommended_action=None,
        proposed_fields={},
        citations=[],
        abstain_reason="unsupported_break_pattern",
    )
    decision = policy.evaluate(recommendation, FACTS)
    assert decision.outcome == "MANUAL_INVESTIGATION"
    assert decision.reasons == ["unsupported_break_pattern"]


# --------------------------------------------------------------------------
# 8. unapproved / hallucinated field rejected (policy layer)
# --------------------------------------------------------------------------
def test_policy_rejects_a_field_outside_the_allow_list() -> None:
    recommendation = _recommendation(proposed_fields={"/payload/quoted_rate": "1.0850"})
    decision = policy.evaluate(recommendation, FACTS)
    assert decision.outcome == "REJECTED"
    assert "field_outside_allow_list" in decision.reasons


def test_policy_rejects_a_value_the_ai_invented_instead_of_the_authoritative_one() -> None:
    recommendation = _recommendation(proposed_fields={"/payload/base_amount": "1.00"})
    decision = policy.evaluate(recommendation, FACTS)
    assert decision.outcome == "REJECTED"
    assert "proposed_value_does_not_match_authoritative_source" in decision.reasons


def test_policy_never_trusts_the_ais_own_required_approvals_or_risk_tier() -> None:
    recommendation = _recommendation(required_approvals=[], risk_tier="LOW")
    decision = policy.evaluate(recommendation, FACTS)
    assert decision.outcome == "ELIGIBLE_FOR_APPROVAL"
    assert decision.required_approvals == ["MAKER", "CHECKER"]


def test_eligible_policy_decision_is_deterministic() -> None:
    first = policy.evaluate(_recommendation(), FACTS)
    second = policy.evaluate(_recommendation(), FACTS)
    assert first == second
    assert first.approved_field_path == "/payload/base_amount"
    assert first.approved_value == "1018000.00"


# --------------------------------------------------------------------------
# 7. modified/expired envelope rejected
# --------------------------------------------------------------------------
class TestEnvelopeIntegrity:
    def _build(self, **overrides: Any) -> envelope_module.ActionEnvelope:  # type: ignore[name-defined]
        kwargs: dict[str, Any] = dict(
            case_id="case_test_001",
            trade_id="trade_test_001",
            tenant_id="tenant_demo",
            portfolio_id="portfolio_sydney",
            field_path="/payload/base_amount",
            approved_value="1018000.00",
            expected_old_value="1019000.00",
            maker_identity="maker.alice",
            checker_identity="checker.bob",
            idempotency_key="idem_case_test_001",
            secret="unit-test-signing-secret",
        )
        kwargs.update(overrides)
        return envelope_module.build_envelope(**kwargs)

    def test_freshly_built_envelope_verifies(self) -> None:
        env = self._build()
        envelope_module.verify_envelope(env, secret="unit-test-signing-secret")

    def test_a_modified_field_is_detected_as_tampered(self) -> None:
        env = self._build()
        tampered = env.model_copy(update={"approved_value": "9999999.99"})
        with pytest.raises(envelope_module.EnvelopeTamperedError):
            envelope_module.verify_envelope(tampered, secret="unit-test-signing-secret")

    def test_wrong_signing_secret_is_detected_as_tampered(self) -> None:
        env = self._build()
        with pytest.raises(envelope_module.EnvelopeTamperedError):
            envelope_module.verify_envelope(env, secret="a-different-secret-entirely")

    def test_expired_envelope_is_rejected(self) -> None:
        env = self._build(ttl_seconds=1)
        after_expiry = env.expires_at + timedelta(seconds=1)
        with pytest.raises(envelope_module.EnvelopeExpiredError):
            envelope_module.verify_envelope(
                env, secret="unit-test-signing-secret", now=after_expiry
            )

    def test_tampering_is_reported_before_expiry_when_both_are_true(self) -> None:
        env = self._build(ttl_seconds=1)
        tampered = env.model_copy(update={"approved_value": "0.01"})
        after_expiry = env.expires_at + timedelta(seconds=1)
        with pytest.raises(envelope_module.EnvelopeTamperedError):
            envelope_module.verify_envelope(
                tampered, secret="unit-test-signing-secret", now=after_expiry
            )

    def test_missing_signing_secret_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(envelope_module.SIGNING_SECRET_ENV_VAR, raising=False)
        with pytest.raises(envelope_module.EnvelopeSigningError):
            envelope_module.build_envelope(
                case_id="case_x",
                trade_id="trade_x",
                tenant_id="tenant_demo",
                portfolio_id="portfolio_sydney",
                field_path="/payload/base_amount",
                approved_value="1.00",
                expected_old_value="2.00",
                maker_identity="maker.alice",
                checker_identity="checker.bob",
                idempotency_key="idem_case_x",
            )


def test_recommendation_content_hash_is_deterministic_across_model_and_persisted_dict() -> None:
    recommendation = _recommendation()
    as_dict = recommendation.model_dump(mode="json")
    assert recommendation_content_hash(recommendation) == recommendation_content_hash(as_dict)
    assert recommendation_content_hash(recommendation) == recommendation_content_hash(
        AIRecommendation(**as_dict)
    )


def test_retrieval_fails_closed_for_a_citation_that_does_not_exist() -> None:
    assert retrieval.citation_exists("RB-999#1") is False
    assert retrieval.citation_supports_action("RB-999#1", "CORRECT_LEGACY_BOOKING_FIELD") is False
