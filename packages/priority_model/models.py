"""Strict contracts for advisory ML priority output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.remediation.models import BreakFacts, Priority

ShapDirection = Literal["RAISES_PRIORITY", "LOWERS_PRIORITY", "NEUTRAL"]


class PriorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ShapContribution(PriorityModel):
    """One feature's contribution to the model's raw log-odds output."""

    feature: str
    feature_value: float
    shap_value: float
    direction: ShapDirection


class PriorityAssessment(PriorityModel):
    """A versioned LightGBM score and its complete local SHAP explanation.

    This is advisory evidence only.  It is intentionally separate from the
    deterministic policy decision that authorises (or rejects) an action.
    """

    provider: Literal["lightgbm"] = "lightgbm"
    model_version: str
    feature_version: str
    training_data: Literal["SYNTHETIC_ONLY"] = "SYNTHETIC_ONLY"
    score: float = Field(ge=0.0, le=1.0)
    priority: Priority
    threshold_policy: str
    raw_score: float
    shap_base_value: float
    shap_contributions: list[ShapContribution] = Field(min_length=1)
    shap_additivity_error: float = Field(ge=0.0, le=0.000001)

    @model_validator(mode="after")
    def _shap_values_reconstruct_the_raw_score(self) -> PriorityAssessment:
        reconstructed = self.shap_base_value + sum(
            item.shap_value for item in self.shap_contributions
        )
        if abs(reconstructed - self.raw_score) > 0.000001:
            raise ValueError("SHAP contributions do not reconstruct the raw model score")
        return self


class PriorityProvider:
    """Small typed seam shared by the live model and test doubles."""

    name: str

    def assess(self, facts: BreakFacts) -> PriorityAssessment:  # pragma: no cover - interface
        raise NotImplementedError


class PriorityModelUnavailableError(RuntimeError):
    """The immutable model tuple cannot be loaded or validated."""


__all__ = [
    "PriorityAssessment",
    "PriorityModelUnavailableError",
    "PriorityProvider",
    "ShapContribution",
    "ShapDirection",
]
