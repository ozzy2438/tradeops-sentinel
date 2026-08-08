"""Local tests for the versioned LightGBM priority and SHAP evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.priority_model.features import FEATURE_NAMES, feature_vector
from packages.priority_model.models import PriorityAssessment
from packages.priority_model.provider import LightGBMPriorityProvider
from packages.priority_model.training import validate
from packages.remediation.models import BreakFacts

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


def test_feature_contract_is_fixed_and_point_in_time() -> None:
    values = feature_vector(FACTS)
    assert len(values) == len(FEATURE_NAMES)
    assert FEATURE_NAMES[:3] == (
        "log10_trade_value",
        "relative_value_gap_bps",
        "product_is_forward",
    )
    assert "approval" not in " ".join(FEATURE_NAMES)
    assert "resolution" not in " ".join(FEATURE_NAMES)
    assert "scenario_truth" not in " ".join(FEATURE_NAMES)


def test_live_artifact_scores_and_explains_the_supported_case() -> None:
    assessment = LightGBMPriorityProvider().assess(FACTS)
    assert assessment.model_version == "priority-lgbm-1.0.0"
    assert assessment.training_data == "SYNTHETIC_ONLY"
    assert assessment.priority == "MEDIUM"
    assert assessment.score == pytest.approx(0.3977008695)
    assert len(assessment.shap_contributions) == len(FEATURE_NAMES)
    assert assessment.shap_contributions[0].feature == "field_is_base_amount"
    reconstructed = assessment.shap_base_value + sum(
        item.shap_value for item in assessment.shap_contributions
    )
    assert reconstructed == pytest.approx(assessment.raw_score, abs=1e-6)


def test_priority_assessment_rejects_non_additive_shap_evidence() -> None:
    document = LightGBMPriorityProvider().assess(FACTS).model_dump(mode="json")
    document["raw_score"] += 0.1
    with pytest.raises(ValidationError, match="do not reconstruct"):
        PriorityAssessment.model_validate(document)


def test_checked_in_artifact_hash_dataset_and_metrics_are_reproducible() -> None:
    result = validate(Path("packages/priority_model/artifacts"))
    assert result["status"] == "ok"
    assert result["validation_metrics"]["roc_auc"] >= 0.80
    assert result["validation_metrics"]["brier_score"] <= 0.16
