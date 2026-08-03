"""Versioned, explicit configuration for deterministic reconciliation.

The configuration is deliberately separate from the evaluator.  A reconciliation
run records the configuration identifier, version, and content hash, so a later
replay can prove which arrival windows and decimal tolerances were used.  The
fixture factory below is not an operational approval: it is a reproducible,
synthetic test configuration until a human owner approves an effective policy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.models import (
    BreakRuleVersion,
    BreakTolerance,
    ContractModel,
    LifecycleStatus,
    ObservationKind,
    ProductType,
    SemanticVersion,
    SourceSystem,
)

MissingSourceKind = Literal["EXECUTION", "CONFIRMATION", "BOOKING"]
EconomicFieldPath = Literal[
    "/payload/base_amount",
    "/payload/terms_amount",
    "/payload/quoted_rate",
]

_SOURCE_SYSTEM_BY_KIND: dict[MissingSourceKind, SourceSystem] = {
    "EXECUTION": "FIX_EXECUTION",
    "CONFIRMATION": "FPML_CONFIRMATION",
    "BOOKING": "MOCK_LEGACY_BOOKING",
}

_MISSING_FIELD_BY_KIND: dict[MissingSourceKind, str] = {
    "EXECUTION": "/source/execution_observation",
    "CONFIRMATION": "/source/confirmation_observation",
    "BOOKING": "/source/booking_observation",
}

_ECONOMIC_FIELDS: tuple[EconomicFieldPath, ...] = (
    "/payload/base_amount",
    "/payload/terms_amount",
    "/payload/quoted_rate",
)


class ArrivalWindowRule(ContractModel):
    """Expected source arrival window for one product and source kind."""

    product_type: ProductType
    observation_kind: MissingSourceKind
    source_system: SourceSystem
    field_path: str
    window_seconds: int = Field(ge=0)
    rule_version: BreakRuleVersion = "1.0.0"

    @model_validator(mode="after")
    def validate_source_shape(self) -> ArrivalWindowRule:
        if self.source_system != _SOURCE_SYSTEM_BY_KIND[self.observation_kind]:
            raise ValueError("source_system must match observation_kind")
        if self.field_path != _MISSING_FIELD_BY_KIND[self.observation_kind]:
            raise ValueError("field_path must match observation_kind")
        return self


class DecimalToleranceRule(ContractModel):
    """Decimal comparison tolerance for one product and canonical field path."""

    product_type: ProductType
    field_path: EconomicFieldPath
    tolerance: BreakTolerance
    rule_version: BreakRuleVersion = "1.0.0"

    @model_validator(mode="after")
    def validate_decimal_tolerance(self) -> DecimalToleranceRule:
        if self.tolerance.mode not in {"NONE", "ABSOLUTE_DECIMAL", "RELATIVE_DECIMAL"}:
            raise ValueError("economic tolerance must be a decimal tolerance mode")
        return self


class LifecycleExpectedStatusRule(ContractModel):
    """Versioned source-kind lifecycle expectation used by the MVP fixtures."""

    observation_kind: ObservationKind
    expected_status: LifecycleStatus
    rule_version: BreakRuleVersion = "1.0.0"


def _default_lifecycle_rules() -> tuple[LifecycleExpectedStatusRule, ...]:
    """Return the labelled E3 source-kind lifecycle relation inputs."""

    return (
        LifecycleExpectedStatusRule(observation_kind="EXECUTION", expected_status="NEW"),
        LifecycleExpectedStatusRule(observation_kind="TRADE_CAPTURE", expected_status="CAPTURED"),
        LifecycleExpectedStatusRule(observation_kind="CONFIRMATION", expected_status="CONFIRMED"),
        LifecycleExpectedStatusRule(observation_kind="BOOKING", expected_status="BOOKED"),
    )


class ReconciliationConfig(ContractModel):
    """Immutable policy input to a reconciliation run."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    config_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    config_version: SemanticVersion
    approval_status: Literal["FIXTURE_ONLY", "OWNER_APPROVED"]
    approval_reference: str | None = None
    effective_from: datetime
    effective_to: datetime | None = None
    arrival_windows: tuple[ArrivalWindowRule, ...] = Field(min_length=6)
    decimal_tolerances: tuple[DecimalToleranceRule, ...] = Field(min_length=6)
    normalisation_rule_version: BreakRuleVersion = "1.0.0"
    detection_rule_version: BreakRuleVersion = "1.0.0"
    lifecycle_rule_version: BreakRuleVersion = "1.0.0"
    priority_rule_version: BreakRuleVersion = "1.0.0"
    lifecycle_expected_statuses: tuple[LifecycleExpectedStatusRule, ...] = Field(
        default_factory=_default_lifecycle_rules
    )

    @model_validator(mode="after")
    def validate_complete_policy(self) -> ReconciliationConfig:
        if self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None:
            raise ValueError("effective_from must include a timezone offset")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None:
                raise ValueError("effective_to must include a timezone offset")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        if self.approval_status == "OWNER_APPROVED" and not self.approval_reference:
            raise ValueError("OWNER_APPROVED configuration requires approval_reference")

        arrival_keys = [(rule.product_type, rule.observation_kind) for rule in self.arrival_windows]
        expected_arrival_keys = [
            (product_type, kind)
            for product_type in ("FX_SPOT", "FX_FORWARD")
            for kind in ("EXECUTION", "CONFIRMATION", "BOOKING")
        ]
        if set(arrival_keys) != set(expected_arrival_keys) or len(arrival_keys) != len(
            expected_arrival_keys
        ):
            raise ValueError("arrival_windows must contain one rule for every product/source kind")

        tolerance_keys = [(rule.product_type, rule.field_path) for rule in self.decimal_tolerances]
        expected_tolerance_keys = [
            (product_type, field_path)
            for product_type in ("FX_SPOT", "FX_FORWARD")
            for field_path in _ECONOMIC_FIELDS
        ]
        if set(tolerance_keys) != set(expected_tolerance_keys) or len(tolerance_keys) != len(
            expected_tolerance_keys
        ):
            raise ValueError(
                "decimal_tolerances must contain one rule for every product/economic field"
            )
        lifecycle_kinds = [rule.observation_kind for rule in self.lifecycle_expected_statuses]
        expected_lifecycle_kinds = ["EXECUTION", "TRADE_CAPTURE", "CONFIRMATION", "BOOKING"]
        if set(lifecycle_kinds) != set(expected_lifecycle_kinds) or len(lifecycle_kinds) != len(
            expected_lifecycle_kinds
        ):
            raise ValueError(
                "lifecycle_expected_statuses must contain one rule for every observation kind"
            )
        return self

    @property
    def content_hash(self) -> str:
        """Return a stable hash of the validated policy document."""

        encoded = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude_none=False),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{sha256(encoded).hexdigest()}"

    def arrival_rule(
        self,
        product_type: ProductType,
        observation_kind: MissingSourceKind,
    ) -> ArrivalWindowRule:
        for rule in self.arrival_windows:
            if (rule.product_type, rule.observation_kind) == (product_type, observation_kind):
                return rule
        raise KeyError((product_type, observation_kind))

    def decimal_rule(
        self,
        product_type: ProductType,
        field_path: EconomicFieldPath,
    ) -> DecimalToleranceRule:
        for rule in self.decimal_tolerances:
            if (rule.product_type, rule.field_path) == (product_type, field_path):
                return rule
        raise KeyError((product_type, field_path))

    def lifecycle_rule(self, observation_kind: ObservationKind) -> LifecycleExpectedStatusRule:
        """Return the explicit lifecycle expectation for one source kind."""

        for rule in self.lifecycle_expected_statuses:
            if rule.observation_kind == observation_kind:
                return rule
        raise KeyError(observation_kind)


def fixture_config() -> ReconciliationConfig:
    """Build the deterministic synthetic configuration used by local tests."""

    arrival_windows = tuple(
        ArrivalWindowRule(
            product_type=product_type,
            observation_kind=observation_kind,
            source_system=_SOURCE_SYSTEM_BY_KIND[observation_kind],
            field_path=_MISSING_FIELD_BY_KIND[observation_kind],
            window_seconds=0,
        )
        for product_type in ("FX_SPOT", "FX_FORWARD")
        for observation_kind in ("EXECUTION", "CONFIRMATION", "BOOKING")
    )
    decimal_tolerances = tuple(
        DecimalToleranceRule(
            product_type=product_type,
            field_path=field_path,
            tolerance=BreakTolerance(
                mode="ABSOLUTE_DECIMAL",
                value="0.01" if field_path != "/payload/quoted_rate" else "0.0001",
            ),
        )
        for product_type in ("FX_SPOT", "FX_FORWARD")
        for field_path in _ECONOMIC_FIELDS
    )
    return ReconciliationConfig(
        config_id="fixture_ts11_default",
        config_version="1.0.0",
        approval_status="FIXTURE_ONLY",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        arrival_windows=arrival_windows,
        decimal_tolerances=decimal_tolerances,
    )
