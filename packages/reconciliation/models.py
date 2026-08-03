"""Typed request, post-action, fact, and run models for TS-11."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.models import (
    Actor,
    BookingObservation,
    BreakConditionCode,
    BreakFamily,
    BreakSeverity,
    BreakSourceReference,
    BreakTolerance,
    BreakValueType,
    CanonicalTradeState,
    ContractModel,
    LinkageDecision,
    ObservationModel,
    Sha256,
    TradeBreak,
)

PostActionFieldPath = Literal[
    "/payload/book_id",
    "/payload/lifecycle_status",
    "/payload/booking_version",
    "/payload/record_fingerprint",
]


class ChangedField(ContractModel):
    """A field difference observed during a read-only post-action verification."""

    field_path: PostActionFieldPath
    expected_value: str = Field(min_length=1)
    observed_value: str = Field(min_length=1)


class PostActionVerification(ContractModel):
    """Evidence supplied by an external action adapter; this package never writes."""

    action_instruction_hash: Sha256
    pre_action: BookingObservation
    post_action: BookingObservation | None = None
    changed_fields: tuple[ChangedField, ...] = ()
    readback_available: bool = True
    original_break_remaining: bool = False

    @model_validator(mode="after")
    def validate_action_observations(self) -> PostActionVerification:
        if self.pre_action.observation_kind != "BOOKING":
            raise ValueError("pre_action must be a BOOKING observation")
        if self.post_action is not None and self.post_action.observation_kind != "BOOKING":
            raise ValueError("post_action must be a BOOKING observation")
        if self.post_action is not None:
            for field_name in (
                "tenant_id",
                "portfolio_id",
                "correlation_id",
                "source_business_key",
            ):
                if getattr(self.pre_action, field_name) != getattr(self.post_action, field_name):
                    raise ValueError(
                        "pre_action and post_action must identify the same scoped trade"
                    )
        if self.readback_available and self.post_action is None:
            raise ValueError("readback_available requires post_action")
        changed_keys = {
            (field.field_path, field.expected_value, field.observed_value)
            for field in self.changed_fields
        }
        if len(self.changed_fields) != len(changed_keys):
            raise ValueError("changed_fields must be unique")
        return self


class ReconciliationContext(ContractModel):
    """Exact immutable input set for one deterministic reconciliation run."""

    reconciliation_run_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    run_version: int = Field(ge=1)
    canonical_state: CanonicalTradeState
    source_observations: tuple[ObservationModel, ...] = Field(min_length=1)
    evaluated_at: datetime | None = None
    linkage_decision: LinkageDecision | None = None
    post_action_verification: PostActionVerification | None = None

    @model_validator(mode="after")
    def validate_context_scope(self) -> ReconciliationContext:
        canonical = self.canonical_state
        if self.evaluated_at is not None:
            if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
                raise ValueError("evaluated_at must include a timezone offset")
            if self.evaluated_at < canonical.source_watermark:
                raise ValueError("evaluated_at must not precede source_watermark")

        observation_ids = [observation.observation_id for observation in self.source_observations]
        if len(observation_ids) != len(set(observation_ids)):
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
                observation.observation_id,
                observation.observation_kind,
                observation.source_system,
                observation.source_version,
                observation.content_hash,
            )
            for observation in self.source_observations
        }
        if actual_keys != expected_keys:
            raise ValueError(
                "source_observations must equal canonical_state.source_version_set exactly"
            )
        linkage_is_resolved = self.linkage_decision is not None and (
            self.linkage_decision.decision == "ACCEPTED"
        )
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
            if linkage_is_resolved and observation.payload.source_trade_id != canonical.trade_id:
                raise ValueError("source_observations must identify the canonical trade")
            if observation.payload.product_type != canonical.state.product_type:
                raise ValueError("source_observations product_type must match canonical state")
            if (
                observation.payload.settlement_rule_version
                != canonical.state.settlement_rule_version
            ):
                raise ValueError(
                    "source_observations settlement_rule_version must match canonical state"
                )

        if self.linkage_decision is not None:
            if (
                self.linkage_decision.tenant_id,
                self.linkage_decision.portfolio_id,
                self.linkage_decision.correlation_id,
            ) != (
                canonical.tenant_id,
                canonical.portfolio_id,
                canonical.correlation_id,
            ):
                raise ValueError("linkage_decision must remain inside canonical scope")
            if self.linkage_decision.source_observation_id not in observation_ids:
                raise ValueError("linkage_decision must reference the exact source set")

        if self.post_action_verification is not None:
            pre_action = self.post_action_verification.pre_action
            if pre_action.observation_id not in observation_ids:
                raise ValueError("pre_action must reference the exact source set")
            if self.post_action_verification.post_action is not None and (
                self.post_action_verification.post_action.observation_id not in observation_ids
            ):
                raise ValueError("post_action must reference the exact source set")
        return self

    @property
    def effective_evaluated_at(self) -> datetime:
        return self.evaluated_at or self.canonical_state.source_watermark


class BreakFact(ContractModel):
    """Compact deterministic fact projection used by queue and evidence consumers."""

    family: BreakFamily
    condition_code: BreakConditionCode
    field_path: str
    value_type: BreakValueType
    expected_value: str
    observed_value: str
    tolerance: BreakTolerance
    severity: BreakSeverity
    expected_source_observation_id: str | None = None
    observed_source_observation_id: str | None = None


class ReconciliationRun(ContractModel):
    """Append-only result envelope for one versioned evaluation."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    run_version: int = Field(ge=1)
    tenant_id: str
    portfolio_id: str
    correlation_id: str
    trade_id: str
    canonical_state_version: int = Field(ge=1)
    source_watermark: datetime
    evaluated_at: datetime
    config_id: str
    config_version: str
    config_hash: Sha256
    detection_rule_version: Literal["1.0.0"] = "1.0.0"
    result: Literal["PASS", "BREAKS_DETECTED"]
    break_ids: tuple[str, ...]
    breaks: tuple[TradeBreak, ...]
    break_facts: tuple[BreakFact, ...]
    source_version_set: tuple[BreakSourceReference, ...] = Field(min_length=1)
    actor: Actor
    content_hash: Sha256

    @model_validator(mode="after")
    def validate_result_shape(self) -> ReconciliationRun:
        if self.source_watermark.tzinfo is None or self.source_watermark.utcoffset() is None:
            raise ValueError("source_watermark must include a timezone offset")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone offset")
        if self.evaluated_at < self.source_watermark:
            raise ValueError("evaluated_at must not precede source_watermark")
        if len(self.break_ids) != len(set(self.break_ids)):
            raise ValueError("break_ids must be unique")
        if tuple(break_item.break_id for break_item in self.breaks) != self.break_ids:
            raise ValueError("break_ids must match breaks in deterministic order")
        if (self.result == "PASS") != (not self.breaks):
            raise ValueError("result must agree with whether breaks were detected")
        for break_item in self.breaks:
            if break_item.reconciliation_run_id != self.run_id:
                raise ValueError("break must bind to this reconciliation run")
            if (
                break_item.tenant_id,
                break_item.portfolio_id,
                break_item.correlation_id,
                break_item.trade_id,
            ) != (self.tenant_id, self.portfolio_id, self.correlation_id, self.trade_id):
                raise ValueError("break must remain inside the run scope")
        expected_fact_count = sum(len(break_item.comparisons) for break_item in self.breaks)
        if len(self.break_facts) != expected_fact_count:
            raise ValueError("break_facts must flatten every break comparison exactly once")
        expected_hash = stable_content_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected_hash:
            raise ValueError("reconciliation run content_hash does not match run content")
        return self


def stable_content_hash(value: object) -> Sha256:
    """Hash a JSON-compatible value using deterministic key ordering."""

    import json

    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"


def utc_now() -> datetime:
    """Return an explicit UTC timestamp for callers that need one."""

    return datetime.now(UTC)
