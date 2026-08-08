"""Immutable LightGBM inference with built-in SHAP contributions."""

from __future__ import annotations

import hashlib
import json
import math
from importlib.resources import as_file, files
from typing import Any

import lightgbm as lgb
import numpy as np

from packages.remediation.models import BreakFacts, Priority

from .features import FEATURE_NAMES, FEATURE_VERSION, feature_vector
from .models import (
    PriorityAssessment,
    PriorityModelUnavailableError,
    PriorityProvider,
    ShapContribution,
    ShapDirection,
)

MODEL_FILENAME = "priority_model.txt"
METADATA_FILENAME = "metadata.json"


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _priority_for_score(score: float, thresholds: dict[str, float]) -> Priority:
    if score >= thresholds["critical"]:
        return "CRITICAL"
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"


def _direction(value: float) -> ShapDirection:
    if value > 0.0:
        return "RAISES_PRIORITY"
    if value < 0.0:
        return "LOWERS_PRIORITY"
    return "NEUTRAL"


class LightGBMPriorityProvider(PriorityProvider):
    """Load one versioned model tuple and score strict ``BreakFacts`` only."""

    name = "lightgbm"

    def __init__(self) -> None:
        artifact_root = files("packages.priority_model").joinpath("artifacts")
        model_resource = artifact_root.joinpath(MODEL_FILENAME)
        metadata_resource = artifact_root.joinpath(METADATA_FILENAME)
        try:
            model_bytes = model_resource.read_bytes()
            metadata = json.loads(metadata_resource.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise PriorityModelUnavailableError(
                "priority model artifact tuple is unavailable"
            ) from error

        if metadata.get("model_sha256") != _sha256(model_bytes):
            raise PriorityModelUnavailableError(
                "priority model artifact hash does not match metadata"
            )
        if tuple(metadata.get("feature_names", ())) != FEATURE_NAMES:
            raise PriorityModelUnavailableError("priority model feature order is incompatible")
        if metadata.get("feature_version") != FEATURE_VERSION:
            raise PriorityModelUnavailableError("priority model feature version is incompatible")
        if metadata.get("training_data") != "SYNTHETIC_ONLY":
            raise PriorityModelUnavailableError(
                "priority model training-data declaration is invalid"
            )

        with as_file(model_resource) as model_path:
            self._booster = lgb.Booster(model_file=str(model_path))
        self._metadata: dict[str, Any] = metadata

    def assess(self, facts: BreakFacts) -> PriorityAssessment:
        values = feature_vector(facts)
        matrix = np.asarray([values], dtype=np.float64)
        probability_output = self._booster.predict(matrix, num_threads=1)
        raw_output = self._booster.predict(matrix, raw_score=True, num_threads=1)
        contribution_output = self._booster.predict(matrix, pred_contrib=True, num_threads=1)

        score = float(np.asarray(probability_output).reshape(-1)[0])
        raw_score = float(np.asarray(raw_output).reshape(-1)[0])
        contribution_row = np.asarray(contribution_output).reshape(1, -1)[0]
        if contribution_row.size != len(FEATURE_NAMES) + 1:
            raise PriorityModelUnavailableError("priority model returned an invalid SHAP shape")

        shap_base_value = float(contribution_row[-1])
        shap_values = [float(value) for value in contribution_row[:-1]]
        reconstructed = shap_base_value + sum(shap_values)
        additivity_error = abs(reconstructed - raw_score)
        if not math.isfinite(score) or not math.isfinite(raw_score) or additivity_error > 0.000001:
            raise PriorityModelUnavailableError(
                "priority model produced invalid inference evidence"
            )

        contributions = [
            ShapContribution(
                feature=name,
                feature_value=round(float(feature_value), 10),
                shap_value=round(shap_value, 10),
                direction=_direction(shap_value),
            )
            for name, feature_value, shap_value in zip(
                FEATURE_NAMES, values, shap_values, strict=True
            )
        ]
        contributions.sort(key=lambda item: abs(item.shap_value), reverse=True)

        thresholds = {
            str(key): float(value) for key, value in self._metadata["priority_thresholds"].items()
        }
        rounded_raw_score = round(raw_score, 10)
        rounded_base = round(shap_base_value, 10)
        rounded_error = abs(
            rounded_base + sum(item.shap_value for item in contributions) - rounded_raw_score
        )
        return PriorityAssessment(
            model_version=str(self._metadata["model_version"]),
            feature_version=FEATURE_VERSION,
            score=round(score, 10),
            priority=_priority_for_score(score, thresholds),
            threshold_policy=str(self._metadata["threshold_policy"]),
            raw_score=rounded_raw_score,
            shap_base_value=rounded_base,
            shap_contributions=contributions,
            shap_additivity_error=round(rounded_error, 12),
        )


__all__ = ["LightGBMPriorityProvider"]
