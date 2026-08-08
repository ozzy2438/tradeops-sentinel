from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from packages.remediation import policy, retrieval
from packages.remediation.ai_provider import (
    AIProviderError,
    AzureOpenAIProvider,
    AzureOpenAISettings,
    AzureProposedField,
    AzureRecommendationOutput,
    get_provider,
)
from packages.remediation.models import AIRecommendation, BreakFacts, Citation

FACTS = BreakFacts(
    break_id="break_azure_test_001",
    break_family="ECONOMIC_VALUE_MISMATCH",
    condition_code="DECIMAL_OUTSIDE_TOLERANCE",
    product_type="FX_SPOT",
    trade_id="trade_azure_test_001",
    field_path="/payload/base_amount",
    expected_value="1018000.00",
    observed_value="1019000.00",
    expected_source_system="FIX_EXECUTION",
    observed_source_system="MOCK_LEGACY_BOOKING",
    trade_value_amount="1018000.00",
    trade_value_currency="EUR",
)


def _recommendation() -> AIRecommendation:
    return AIRecommendation(
        predicted_root_cause="Legacy booking differs from the authoritative execution value.",
        confidence=0.94,
        priority="HIGH",
        recommended_action="CORRECT_LEGACY_BOOKING_FIELD",
        proposed_fields={"/payload/base_amount": "1018000.00"},
        risk_tier="MEDIUM",
        required_approvals=["MAKER", "CHECKER"],
        citations=[Citation(document_id="RB-001", section="3")],
        abstain_reason=None,
    )


def _azure_output() -> AzureRecommendationOutput:
    recommendation = _recommendation()
    return AzureRecommendationOutput(
        predicted_root_cause=recommendation.predicted_root_cause,
        confidence=recommendation.confidence,
        priority=recommendation.priority,
        recommended_action=recommendation.recommended_action,
        proposed_fields=[
            AzureProposedField(field_path=field_path, value=value)
            for field_path, value in recommendation.proposed_fields.items()
        ],
        risk_tier=recommendation.risk_tier,
        required_approvals=recommendation.required_approvals,
        citations=recommendation.citations,
        abstain_reason=recommendation.abstain_reason,
    )


class FakeAzureClient:
    def __init__(self, parsed: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.parsed = parsed
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        )

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=self.parsed, refusal=None))]
        )


def test_azure_provider_uses_bounded_structured_output_and_existing_policy() -> None:
    fake = FakeAzureClient(_azure_output())
    settings = AzureOpenAISettings(endpoint="https://example.openai.azure.com")
    provider = AzureOpenAIProvider(settings=settings, client=fake)
    candidates = retrieval.search("base_amount booking correction", top_k=5)

    result = provider.recommend(facts=FACTS, candidate_citations=candidates)
    decision = policy.evaluate(result, FACTS)

    assert provider.name == "azure-openai:gpt-5.4-mini"
    assert decision.outcome == "ELIGIBLE_FOR_APPROVAL"
    assert fake.calls[0]["model"] == "gpt-5.4-mini"
    assert fake.calls[0]["response_format"] is AzureRecommendationOutput
    assert fake.calls[0]["max_completion_tokens"] == 400
    assert fake.calls[0]["reasoning_effort"] == "minimal"


def test_ai_recommendation_schema_requires_every_structured_output_field() -> None:
    schema = AIRecommendation.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])

    azure_schema = AzureRecommendationOutput.model_json_schema()
    assert set(azure_schema["required"]) == set(azure_schema["properties"])
    assert azure_schema["properties"]["proposed_fields"]["type"] == "array"


def test_azure_provider_fails_closed_without_structured_output() -> None:
    provider = AzureOpenAIProvider(
        settings=AzureOpenAISettings(endpoint="https://example.openai.azure.com"),
        client=FakeAzureClient(None),
    )
    with pytest.raises(AIProviderError, match="no structured recommendation"):
        provider.recommend(facts=FACTS, candidate_citations=[])


def test_unknown_provider_name_fails_closed() -> None:
    with pytest.raises(AIProviderError, match="unknown TRADEOPS_AI_PROVIDER"):
        get_provider("misspelled-live-provider")
