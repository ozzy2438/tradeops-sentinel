"""Typed adapter boundary for callers that already have shared contracts.

The public oracle evaluator still projects these models to JSON before applying
its independent rules.  This module imports shared contracts only; it never
imports production reconciliation models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.models import (
    BookingObservation,
    BreakFamily,
    CanonicalTradeState,
    ContractModel,
    LinkageDecision,
    ObservationModel,
    Sha256,
)

OracleFieldPath = Literal[
    "/payload/book_id",
    "/payload/lifecycle_status",
    "/payload/booking_version",
    "/payload/record_fingerprint",
]


class OracleChangedField(ContractModel):
    """A read-only post-action field difference."""

    field_path: OracleFieldPath
    expected_value: str = Field(min_length=1)
    observed_value: str = Field(min_length=1)


class OraclePostAction(ContractModel):
    """Post-action evidence represented without production reconciliation types."""

    action_instruction_hash: Sha256
    pre_action: BookingObservation
    post_action: BookingObservation | None = None
    changed_fields: tuple[OracleChangedField, ...] = ()
    readback_available: bool = True
    original_break_remaining: bool = False

    @model_validator(mode="after")
    def validate_observations(self) -> OraclePostAction:
        if self.post_action is not None:
            for field_name in (
                "tenant_id",
                "portfolio_id",
                "correlation_id",
                "source_business_key",
            ):
                if getattr(self.pre_action, field_name) != getattr(self.post_action, field_name):
                    raise ValueError("pre_action and post_action must identify one scoped trade")
        if self.readback_available and self.post_action is None:
            raise ValueError("readback_available requires post_action")
        changed_keys = {
            (item.field_path, item.expected_value, item.observed_value)
            for item in self.changed_fields
        }
        if len(changed_keys) != len(self.changed_fields):
            raise ValueError("changed_fields must be unique")
        return self


class OracleContext(ContractModel):
    """Exact source-version and watermark input accepted by the adapter."""

    canonical_state: CanonicalTradeState
    source_observations: tuple[ObservationModel, ...] = Field(min_length=1)
    evaluated_at: datetime | None = None
    linkage_decision: LinkageDecision | None = None
    post_action: OraclePostAction | None = None

    @model_validator(mode="after")
    def validate_input_boundary(self) -> OracleContext:
        canonical = self.canonical_state
        if self.evaluated_at is not None:
            if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
                raise ValueError("evaluated_at must include a timezone offset")
            if self.evaluated_at < canonical.source_watermark:
                raise ValueError("evaluated_at must not precede source_watermark")
        source_ids = [item.observation_id for item in self.source_observations]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_observations observation IDs must be unique")
        expected_keys = {
            (
                item.observation_id,
                item.observation_kind,
                item.source_system,
                item.source_version,
                item.content_hash,
            )
            for item in canonical.source_version_set
        }
        actual_keys = {
            (
                item.observation_id,
                item.observation_kind,
                item.source_system,
                item.source_version,
                item.content_hash,
            )
            for item in self.source_observations
        }
        if actual_keys != expected_keys:
            raise ValueError("source_observations must equal source_version_set exactly")
        for observation in self.source_observations:
            if (
                observation.tenant_id,
                observation.portfolio_id,
                observation.correlation_id,
            ) != (
                canonical.tenant_id,
                canonical.portfolio_id,
                canonical.correlation_id,
            ):
                raise ValueError("source_observations must remain inside canonical scope")
            if observation.ingest_time > canonical.source_watermark:
                raise ValueError("source_observations must not arrive after source_watermark")
        if self.linkage_decision is not None:
            if self.linkage_decision.source_observation_id not in source_ids:
                raise ValueError("linkage_decision must reference the exact source set")
        if self.post_action is not None:
            if self.post_action.pre_action.observation_id not in source_ids:
                raise ValueError("pre_action must reference the exact source set")
            if (
                self.post_action.post_action is not None
                and self.post_action.post_action.observation_id not in source_ids
            ):
                raise ValueError("post_action must reference the exact source set")
        return self

    @property
    def effective_evaluated_at(self) -> datetime:
        """Return the explicit evaluation time or the source watermark."""

        return self.evaluated_at or self.canonical_state.source_watermark


class OraclePolicy(ContractModel):
    """Fixture-only policy identity used by typed callers."""

    policy_id: Literal["ts12_fixture_oracle_policy"] = "ts12_fixture_oracle_policy"
    policy_version: Literal["1.0.0"] = "1.0.0"


class OracleFinding(ContractModel):
    """Optional typed projection for consumers of family-level findings."""

    family: BreakFamily
    field_path: str
