"""Run one bounded Azure recommendation through the remediation contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.remediation import policy, retrieval  # noqa: E402
from packages.remediation.ai_provider import (  # noqa: E402
    AzureOpenAIProvider,
    AzureOpenAISettings,
)
from packages.remediation.models import BreakFacts  # noqa: E402


def synthetic_facts() -> BreakFacts:
    return BreakFacts(
        break_id="break_azure_demo_001",
        break_family="ECONOMIC_VALUE_MISMATCH",
        condition_code="DECIMAL_OUTSIDE_TOLERANCE",
        product_type="FX_SPOT",
        trade_id="trade_azure_demo_001",
        field_path="/payload/base_amount",
        expected_value="1018000.00",
        observed_value="1019000.00",
        expected_source_system="FIX_EXECUTION",
        observed_source_system="MOCK_LEGACY_BOOKING",
        trade_value_amount="1018000.00",
        trade_value_currency="EUR",
    )


def main() -> int:
    facts = synthetic_facts()
    candidates = retrieval.search(
        "ECONOMIC_VALUE_MISMATCH base_amount FIX_EXECUTION MOCK_LEGACY_BOOKING correction approval",
        top_k=5,
    )
    provider = AzureOpenAIProvider(settings=AzureOpenAISettings.from_env())
    recommendation = provider.recommend(facts=facts, candidate_citations=candidates)
    decision = policy.evaluate(recommendation, facts)
    print(
        json.dumps(
            {
                "provider": provider.name,
                "recommendation": recommendation.model_dump(mode="json"),
                "policy_decision": decision.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if decision.outcome == "ELIGIBLE_FOR_APPROVAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
