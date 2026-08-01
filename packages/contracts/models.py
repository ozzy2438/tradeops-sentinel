"""Pydantic v2 models for the TS-3 canonical FX contract.

The JSON Schemas in ``schemas/`` are the interchange contract. These models
add the cross-field rules that JSON Schema alone cannot express, while
remaining deliberately free of persistence, reconciliation, or transport
behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from re import fullmatch
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SchemaVersion: TypeAlias = Literal["1.0.0"]
SourceSystem: TypeAlias = Literal[
    "FIX_EXECUTION",
    "FIX_TRADE_CAPTURE",
    "FPML_CONFIRMATION",
    "MOCK_LEGACY_BOOKING",
]
ObservationKind: TypeAlias = Literal[
    "EXECUTION",
    "TRADE_CAPTURE",
    "CONFIRMATION",
    "BOOKING",
]
ProductType: TypeAlias = Literal["FX_SPOT", "FX_FORWARD"]
Side: TypeAlias = Literal["BUY_BASE", "SELL_BASE"]
LifecycleStatus: TypeAlias = Literal[
    "NEW",
    "CAPTURED",
    "CONFIRMED",
    "BOOKED",
    "AMENDED",
    "CANCELLED",
    "SETTLED",
]
ConflictStatus: TypeAlias = Literal[
    "SELECTED",
    "SECONDARY_SUPPORTING",
    "CONFLICTING",
    "UNAVAILABLE",
]
DecimalValue = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"),
]
Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]{2,127}$"),
]
TenantId = Annotated[
    str,
    StringConstraints(pattern=r"^tenant_[a-z0-9][a-z0-9_-]{1,63}$"),
]
PortfolioId = Annotated[
    str,
    StringConstraints(pattern=r"^portfolio_[a-z0-9][a-z0-9_-]{1,63}$"),
]
CorrelationId = Annotated[
    str,
    StringConstraints(pattern=r"^corr_[a-z0-9][a-z0-9_-]{1,127}$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
SemanticVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"),
]
SettlementRuleVersion: TypeAlias = Literal["1.0.0"]
SourceVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[1-9][0-9]{0,18}$"),
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
FieldPath = Annotated[
    str,
    StringConstraints(pattern=r"^/payload/[a-z][a-z0-9_/-]*$"),
]


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value


AwareTimestamp = Annotated[datetime, AfterValidator(_require_timezone)]


_SOURCE_SYSTEM_BY_OBSERVATION_KIND: dict[str, str] = {
    "EXECUTION": "FIX_EXECUTION",
    "TRADE_CAPTURE": "FIX_TRADE_CAPTURE",
    "CONFIRMATION": "FPML_CONFIRMATION",
    "BOOKING": "MOCK_LEGACY_BOOKING",
}

_CANONICAL_FIELD_NAMES = (
    "product_type",
    "settlement_rule_version",
    "base_currency",
    "terms_currency",
    "side",
    "base_amount",
    "terms_amount",
    "quoted_rate",
    "trade_date",
    "value_date",
    "lifecycle_status",
    "counterparty_id",
    "book_id",
)
_SOURCE_OF_TRUTH_FIELD_PATHS = tuple(
    f"/payload/{field_name}" for field_name in _CANONICAL_FIELD_NAMES
) + ("/payload/confirmation_status", "/payload/booking_status", "/linkage/trade_id")


class ContractModel(BaseModel):
    """Shared strict configuration for externally supplied contract data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DecimalAmount(ContractModel):
    currency: Currency
    value: DecimalValue
    scale: int = Field(ge=0, le=18)

    @model_validator(mode="after")
    def scale_matches_value(self) -> DecimalAmount:
        fractional_digits = len(self.value.partition(".")[2])
        if fractional_digits != self.scale:
            raise ValueError("decimal value must contain exactly the declared scale")
        return self


class DecimalRate(ContractModel):
    value: DecimalValue
    scale: int = Field(ge=0, le=18)
    orientation: Literal["TERMS_CURRENCY_PER_BASE_CURRENCY"]

    @model_validator(mode="after")
    def scale_matches_value(self) -> DecimalRate:
        fractional_digits = len(self.value.partition(".")[2])
        if fractional_digits != self.scale:
            raise ValueError("decimal value must contain exactly the declared scale")
        return self


class Actor(ContractModel):
    identity_type: Literal["SYSTEM", "HUMAN", "AGENT", "SOURCE"]
    actor_id: Identifier


class FxPayload(ContractModel):
    product_type: ProductType
    settlement_rule_version: SettlementRuleVersion
    source_trade_id: Identifier
    base_currency: Currency
    terms_currency: Currency
    side: Side
    base_amount: DecimalAmount
    terms_amount: DecimalAmount
    quoted_rate: DecimalRate
    trade_date: date
    value_date: date
    counterparty_id: Identifier
    book_id: Identifier
    lifecycle_status: LifecycleStatus

    @model_validator(mode="after")
    def validate_currency_and_dates(self) -> FxPayload:
        if self.base_currency == self.terms_currency:
            raise ValueError("base_currency and terms_currency must differ")
        if self.base_amount.currency != self.base_currency:
            raise ValueError("base_amount.currency must equal base_currency")
        if self.terms_amount.currency != self.terms_currency:
            raise ValueError("terms_amount.currency must equal terms_currency")
        _validate_settlement_window(
            self.product_type,
            self.trade_date,
            self.value_date,
        )
        return self


class ObservationEnvelope(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    observation_id: Identifier
    observation_kind: ObservationKind
    entity_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    source_system: SourceSystem
    source_event_id: Identifier
    source_business_key: Identifier
    source_version: SourceVersion
    content_hash: Sha256
    event_time: AwareTimestamp
    effective_time: AwareTimestamp
    ingest_time: AwareTimestamp
    source_sequence: int = Field(ge=0)
    lineage_group_id: Identifier
    actor: Actor
    supersedes_observation_id: Identifier | None = None
    supersession_reason: Literal["CORRECTION", "LATE_REVISION", "SOURCE_AMENDMENT"] | None = None
    payload: FxPayload

    @model_validator(mode="after")
    def validate_time_and_supersession(self) -> ObservationEnvelope:
        if self.entity_version != 1:
            raise ValueError(
                "observation entity_version must be 1; revisions create a new observation_id"
            )
        if self.event_time > self.ingest_time:
            raise ValueError("event_time must not be after ingest_time")
        for field_name in (
            "execution_time",
            "capture_time",
            "confirmation_time",
            "last_updated_time",
        ):
            timestamp = getattr(self.payload, field_name, None)
            if timestamp is not None and timestamp > self.ingest_time:
                raise ValueError(f"payload.{field_name} must not be after ingest_time")
        if self.supersedes_observation_id is None and self.supersession_reason is not None:
            raise ValueError("supersession_reason requires supersedes_observation_id")
        if self.supersedes_observation_id is not None and self.supersession_reason is None:
            raise ValueError("supersedes_observation_id requires supersession_reason")
        return self


class ExecutionPayload(FxPayload):
    execution_id: Identifier
    execution_type: Literal["NEW", "AMEND", "CANCEL"]
    execution_status: Literal["EXECUTED", "AMENDED", "CANCELLED"]
    execution_time: AwareTimestamp
    order_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_execution_status(self) -> ExecutionPayload:
        expected = {
            "NEW": ("NEW", "EXECUTED", "NEW"),
            "AMEND": ("AMEND", "AMENDED", "AMENDED"),
            "CANCEL": ("CANCEL", "CANCELLED", "CANCELLED"),
        }[self.execution_type]
        if (self.execution_type, self.execution_status, self.lifecycle_status) != expected:
            raise ValueError("execution type, status, and lifecycle_status are inconsistent")
        return self


class ExecutionObservation(ObservationEnvelope):
    observation_kind: Literal["EXECUTION"] = "EXECUTION"
    source_system: Literal["FIX_EXECUTION"] = "FIX_EXECUTION"
    payload: ExecutionPayload


class TradeCapturePayload(FxPayload):
    capture_id: Identifier
    capture_type: Literal["NEW", "AMEND", "CANCEL"]
    capture_status: Literal["CAPTURED", "AMENDED", "CANCELLED"]
    capture_time: AwareTimestamp
    execution_reference: Identifier

    @model_validator(mode="after")
    def validate_capture_status(self) -> TradeCapturePayload:
        expected = {
            "NEW": ("NEW", "CAPTURED", "CAPTURED"),
            "AMEND": ("AMEND", "AMENDED", "AMENDED"),
            "CANCEL": ("CANCEL", "CANCELLED", "CANCELLED"),
        }[self.capture_type]
        if (self.capture_type, self.capture_status, self.lifecycle_status) != expected:
            raise ValueError("capture type, status, and lifecycle_status are inconsistent")
        return self


class TradeCaptureObservation(ObservationEnvelope):
    observation_kind: Literal["TRADE_CAPTURE"] = "TRADE_CAPTURE"
    source_system: Literal["FIX_TRADE_CAPTURE"] = "FIX_TRADE_CAPTURE"
    payload: TradeCapturePayload


class ConfirmationPayload(FxPayload):
    confirmation_id: Identifier
    confirmation_reference: str = Field(min_length=1, max_length=128)
    confirmation_status: Literal["PENDING", "AFFIRMED", "REJECTED", "AMENDED", "CANCELLED"]
    confirmation_time: AwareTimestamp
    fpml_profile: Literal["fpml-style-fx-v1"]

    @model_validator(mode="after")
    def validate_confirmation_status(self) -> ConfirmationPayload:
        expected_lifecycle = {
            "PENDING": "CAPTURED",
            "AFFIRMED": "CONFIRMED",
            "REJECTED": "CANCELLED",
            "AMENDED": "AMENDED",
            "CANCELLED": "CANCELLED",
        }[self.confirmation_status]
        if self.lifecycle_status != expected_lifecycle:
            raise ValueError("confirmation status and lifecycle_status are inconsistent")
        return self


class ConfirmationObservation(ObservationEnvelope):
    observation_kind: Literal["CONFIRMATION"] = "CONFIRMATION"
    source_system: Literal["FPML_CONFIRMATION"] = "FPML_CONFIRMATION"
    payload: ConfirmationPayload


class BookingPayload(FxPayload):
    booking_record_id: Identifier
    booking_version: int = Field(ge=1)
    booking_status: Literal["BOOKED", "AMENDED", "CANCELLED"]
    last_updated_time: AwareTimestamp
    confirmation_reference: str | None = Field(default=None, max_length=128)
    record_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_booking_status(self) -> BookingPayload:
        if self.booking_status != self.lifecycle_status:
            raise ValueError("booking_status and lifecycle_status are inconsistent")
        return self


class BookingObservation(ObservationEnvelope):
    observation_kind: Literal["BOOKING"] = "BOOKING"
    source_system: Literal["MOCK_LEGACY_BOOKING"] = "MOCK_LEGACY_BOOKING"
    payload: BookingPayload


class CanonicalFields(ContractModel):
    product_type: ProductType
    settlement_rule_version: SettlementRuleVersion
    base_currency: Currency
    terms_currency: Currency
    side: Side
    base_amount: DecimalAmount
    terms_amount: DecimalAmount
    quoted_rate: DecimalRate
    trade_date: date
    value_date: date
    lifecycle_status: LifecycleStatus
    counterparty_id: Identifier
    book_id: Identifier

    @model_validator(mode="after")
    def validate_currency_and_dates(self) -> CanonicalFields:
        if self.base_currency == self.terms_currency:
            raise ValueError("base_currency and terms_currency must differ")
        if self.base_amount.currency != self.base_currency:
            raise ValueError("base_amount.currency must equal base_currency")
        if self.terms_amount.currency != self.terms_currency:
            raise ValueError("terms_amount.currency must equal terms_currency")
        _validate_settlement_window(
            self.product_type,
            self.trade_date,
            self.value_date,
        )
        return self


class FieldProvenance(ContractModel):
    source_type: ObservationKind
    source_system: SourceSystem
    source_tenant_id: TenantId
    source_portfolio_id: PortfolioId
    source_observation_id: Identifier
    source_observation_entity_version: int = Field(ge=1)
    source_version: SourceVersion
    field_path: FieldPath
    normalisation_rule_id: Identifier
    normalisation_rule_version: SemanticVersion
    resolution_rule_version: SemanticVersion
    observed_at: AwareTimestamp
    effective_at: AwareTimestamp
    ingested_at: AwareTimestamp
    conflict_status: ConflictStatus

    @model_validator(mode="after")
    def source_system_matches_kind(self) -> FieldProvenance:
        expected_source_system = _SOURCE_SYSTEM_BY_OBSERVATION_KIND[self.source_type]
        if self.source_system != expected_source_system:
            raise ValueError("source_system must match source_type")
        return self


class FieldProvenanceMap(ContractModel):
    product_type: FieldProvenance
    settlement_rule_version: FieldProvenance
    base_currency: FieldProvenance
    terms_currency: FieldProvenance
    side: FieldProvenance
    base_amount: FieldProvenance
    terms_amount: FieldProvenance
    quoted_rate: FieldProvenance
    trade_date: FieldProvenance
    value_date: FieldProvenance
    lifecycle_status: FieldProvenance
    counterparty_id: FieldProvenance
    book_id: FieldProvenance

    @model_validator(mode="after")
    def field_paths_match_keys(self) -> FieldProvenanceMap:
        for field_name in _CANONICAL_FIELD_NAMES:
            provenance = getattr(self, field_name)
            expected_path = f"/payload/{field_name}"
            if provenance.field_path != expected_path:
                raise ValueError(
                    f"field_provenance.{field_name}.field_path must be {expected_path}"
                )
        return self


class CanonicalTrade(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    trade_id: Identifier
    entity_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    content_hash: Sha256
    created_at: AwareTimestamp
    updated_at: AwareTimestamp
    actor: Actor
    linkage_decision_id: Identifier
    state: CanonicalFields
    field_provenance: FieldProvenanceMap

    @model_validator(mode="after")
    def updated_after_created(self) -> CanonicalTrade:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        _validate_provenance_scope(self.field_provenance, self.tenant_id, self.portfolio_id)
        return self


class SourceVersionSetItem(ContractModel):
    observation_id: Identifier
    observation_kind: ObservationKind
    source_system: SourceSystem
    source_tenant_id: TenantId
    source_portfolio_id: PortfolioId
    source_version: SourceVersion
    content_hash: Sha256

    @model_validator(mode="after")
    def source_system_matches_kind(self) -> SourceVersionSetItem:
        expected_source_system = _SOURCE_SYSTEM_BY_OBSERVATION_KIND[self.observation_kind]
        if self.source_system != expected_source_system:
            raise ValueError("source_system must match observation_kind")
        return self


def _validate_provenance_scope(
    field_provenance: FieldProvenanceMap,
    tenant_id: str,
    portfolio_id: str,
) -> None:
    for field_name in _CANONICAL_FIELD_NAMES:
        provenance = getattr(field_provenance, field_name)
        if (provenance.source_tenant_id, provenance.source_portfolio_id) != (
            tenant_id,
            portfolio_id,
        ):
            raise ValueError(f"field_provenance.{field_name} is outside canonical scope")


def _validate_source_version_set_scope(
    source_version_set: list[SourceVersionSetItem],
    field_provenance: FieldProvenanceMap,
    tenant_id: str,
    portfolio_id: str,
    source_watermark: AwareTimestamp | None = None,
) -> None:
    source_ids = [item.observation_id for item in source_version_set]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source_version_set observation_id values must be unique")
    indexed_sources = {item.observation_id: item for item in source_version_set}
    for item in source_version_set:
        if (item.source_tenant_id, item.source_portfolio_id) != (tenant_id, portfolio_id):
            raise ValueError("source_version_set contains a source outside canonical scope")

    for field_name in _CANONICAL_FIELD_NAMES:
        provenance = getattr(field_provenance, field_name)
        source = indexed_sources.get(provenance.source_observation_id)
        if source is None:
            raise ValueError(f"field_provenance.{field_name} must reference source_version_set")
        if (
            source.observation_kind != provenance.source_type
            or source.source_system != provenance.source_system
            or source.source_tenant_id != provenance.source_tenant_id
            or source.source_portfolio_id != provenance.source_portfolio_id
            or source.source_version != provenance.source_version
        ):
            raise ValueError(f"field_provenance.{field_name} does not match source_version_set")
        if source_watermark is not None and provenance.ingested_at > source_watermark:
            raise ValueError(f"field_provenance.{field_name}.ingested_at is after source_watermark")


def _validate_settlement_window(
    product_type: ProductType,
    trade_date: date,
    value_date: date,
) -> None:
    if value_date < trade_date:
        raise ValueError("value_date must not precede trade_date")
    if product_type == "FX_FORWARD" and value_date <= trade_date:
        raise ValueError("FX_FORWARD value_date must be after trade_date")
    if product_type == "FX_SPOT" and (value_date - trade_date).days > 4:
        raise ValueError(
            "FX_SPOT value_date must be within the versioned T+0-to-T+2 business-day envelope"
        )


class CanonicalTradeState(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    trade_id: Identifier
    entity_version: int = Field(ge=1)
    canonical_state_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    content_hash: Sha256
    as_of_time: AwareTimestamp
    source_watermark: AwareTimestamp
    source_version_set: list[SourceVersionSetItem] = Field(min_length=1)
    actor: Actor
    state: CanonicalFields
    field_provenance: FieldProvenanceMap

    @model_validator(mode="after")
    def validate_state_scope_and_availability(self) -> CanonicalTradeState:
        if self.source_watermark > self.as_of_time:
            raise ValueError("source_watermark must not be after as_of_time")
        _validate_provenance_scope(self.field_provenance, self.tenant_id, self.portfolio_id)
        _validate_source_version_set_scope(
            self.source_version_set,
            self.field_provenance,
            self.tenant_id,
            self.portfolio_id,
            self.source_watermark,
        )
        return self


class CandidateLink(ContractModel):
    trade_id: Identifier
    tenant_id: TenantId
    portfolio_id: PortfolioId
    match_key: Identifier
    match_rule_version: SemanticVersion
    evidence_hash: Sha256


LinkageDecisionValue: TypeAlias = Literal[
    "ACCEPTED",
    "REJECTED",
    "UNMATCHED",
    "AMBIGUOUS",
    "CROSS_SCOPE_REJECTED",
]


class LinkageDecision(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    decision_id: Identifier
    entity_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    content_hash: Sha256
    created_at: AwareTimestamp
    actor: Actor
    source_observation_id: Identifier
    deterministic_rule_version: SemanticVersion
    decision: LinkageDecisionValue
    candidate_links: list[CandidateLink]
    chosen_trade_id: Identifier | None
    reason_code: Literal[
        "EXACT_DETERMINISTIC_KEY",
        "NO_ELIGIBLE_CANDIDATE",
        "MULTIPLE_ELIGIBLE_CANDIDATES",
        "TENANT_OR_PORTFOLIO_SCOPE_MISMATCH",
        "HUMAN_REJECTED",
    ]

    @model_validator(mode="after")
    def validate_decision_shape(self) -> LinkageDecision:
        expected_reason = {
            "ACCEPTED": "EXACT_DETERMINISTIC_KEY",
            "REJECTED": "HUMAN_REJECTED",
            "UNMATCHED": "NO_ELIGIBLE_CANDIDATE",
            "AMBIGUOUS": "MULTIPLE_ELIGIBLE_CANDIDATES",
            "CROSS_SCOPE_REJECTED": "TENANT_OR_PORTFOLIO_SCOPE_MISMATCH",
        }[self.decision]
        if self.reason_code != expected_reason:
            raise ValueError("decision and reason_code combination is not permitted")

        candidate_scope_mismatches = [
            candidate
            for candidate in self.candidate_links
            if (candidate.tenant_id, candidate.portfolio_id) != (self.tenant_id, self.portfolio_id)
        ]
        if self.decision == "CROSS_SCOPE_REJECTED":
            if not self.candidate_links or not candidate_scope_mismatches:
                raise ValueError(
                    "cross-scope rejection requires at least one cross-scope candidate"
                )
        elif candidate_scope_mismatches:
            raise ValueError(
                "non-cross-scope linkage decisions cannot contain cross-scope candidates"
            )

        if self.decision == "ACCEPTED":
            if self.chosen_trade_id is None or len(self.candidate_links) != 1:
                raise ValueError("accepted linkage requires exactly one chosen candidate")
            candidate = self.candidate_links[0]
            if self.chosen_trade_id != candidate.trade_id:
                raise ValueError("accepted linkage must choose its sole candidate")
        elif self.decision == "UNMATCHED" and self.candidate_links:
            raise ValueError("unmatched linkage must not contain candidates")
        elif self.decision == "AMBIGUOUS" and len(self.candidate_links) < 2:
            raise ValueError("ambiguous linkage requires multiple candidates")
        elif self.chosen_trade_id is not None:
            raise ValueError("non-accepted linkage must not choose a trade")
        return self


class IdentityCondition(ContractModel):
    description: str = Field(min_length=1)
    same_delivery_identity: bool | None = None
    same_source_identity: bool | None = None
    same_content_hash: bool | None = None
    source_version_relation: Literal["SAME", "GREATER", "LOWER", "NOT_APPLICABLE"] | None = None
    scope_match: bool | None = None
    schema_version_supported: bool | None = None


class IdentityOutcome(ContractModel):
    precedence: int = Field(ge=1)
    case_id: Identifier
    condition: IdentityCondition
    outcome: Literal[
        "NEW_OBSERVATION",
        "IDEMPOTENT_REPLAY",
        "DUPLICATE_SOURCE_CONFLICT",
        "NEW_SOURCE_VERSION",
        "LATE_SOURCE_VERSION_RECORDED",
        "REJECT_UNSUPPORTED_SCHEMA_VERSION",
        "REJECT_CROSS_PORTFOLIO_LINKAGE",
        "LINKAGE_REVIEW_REQUIRED",
    ]
    storage_effect: Literal[
        "APPEND",
        "APPEND_DUPLICATE_EVIDENCE",
        "APPEND_CONFLICT_EVIDENCE",
        "APPEND_AND_SUPERSEDE_ACTIVE_VERSION",
        "APPEND_LATE_NON_ACTIVE_VERSION",
        "REJECT",
    ]
    canonical_effect: Literal[
        "CREATE_PROJECTION_CANDIDATE",
        "NO_CHANGE",
        "RECONCILE_NEW_SOURCE_SET",
        "DO_NOT_POPULATE_AUTHORITATIVE_FIELDS",
        "REQUIRE_LINKAGE_REVIEW",
    ]


class IdentityPolicy(ContractModel):
    policy_version: SemanticVersion
    source_identity_fields: list[str]
    source_version_field: Literal["source_version"]
    delivery_identity_fields: list[str]
    content_hash_field: Literal["content_hash"] = "content_hash"
    outcomes: list[IdentityOutcome] = Field(min_length=8)

    @model_validator(mode="after")
    def validate_deterministic_table(self) -> IdentityPolicy:
        required_source = [
            "tenant_id",
            "portfolio_id",
            "source_system",
            "observation_kind",
            "source_business_key",
        ]
        if self.source_identity_fields != required_source:
            raise ValueError("source_identity_fields must match the approved identity tuple")
        if self.delivery_identity_fields != ["source_system", "source_event_id"]:
            raise ValueError("delivery_identity_fields must match the approved delivery tuple")
        precedences = [row.precedence for row in self.outcomes]
        if (
            len(self.outcomes) != 8
            or len(set(precedences)) != len(precedences)
            or set(precedences) != set(range(1, len(precedences) + 1))
        ):
            raise ValueError("identity outcomes require unique contiguous precedence values")
        outcomes = {row.outcome for row in self.outcomes}
        required_outcomes = {
            "NEW_OBSERVATION",
            "IDEMPOTENT_REPLAY",
            "DUPLICATE_SOURCE_CONFLICT",
            "NEW_SOURCE_VERSION",
            "LATE_SOURCE_VERSION_RECORDED",
            "REJECT_UNSUPPORTED_SCHEMA_VERSION",
            "REJECT_CROSS_PORTFOLIO_LINKAGE",
            "LINKAGE_REVIEW_REQUIRED",
        }
        if outcomes != required_outcomes:
            raise ValueError("identity policy must define exactly the eight approved outcomes")

        expected_rows: dict[str, tuple[dict[str, Any], str, str]] = {
            "NEW_OBSERVATION": (
                {"same_delivery_identity": False, "same_source_identity": False},
                "APPEND",
                "CREATE_PROJECTION_CANDIDATE",
            ),
            "IDEMPOTENT_REPLAY": (
                {"same_delivery_identity": True, "same_content_hash": True},
                "APPEND_DUPLICATE_EVIDENCE",
                "NO_CHANGE",
            ),
            "DUPLICATE_SOURCE_CONFLICT": (
                {"same_delivery_identity": True, "same_content_hash": False},
                "APPEND_CONFLICT_EVIDENCE",
                "REQUIRE_LINKAGE_REVIEW",
            ),
            "NEW_SOURCE_VERSION": (
                {"same_source_identity": True, "source_version_relation": "GREATER"},
                "APPEND_AND_SUPERSEDE_ACTIVE_VERSION",
                "RECONCILE_NEW_SOURCE_SET",
            ),
            "LATE_SOURCE_VERSION_RECORDED": (
                {"same_source_identity": True, "source_version_relation": "LOWER"},
                "APPEND_LATE_NON_ACTIVE_VERSION",
                "NO_CHANGE",
            ),
            "REJECT_UNSUPPORTED_SCHEMA_VERSION": (
                {"schema_version_supported": False},
                "REJECT",
                "DO_NOT_POPULATE_AUTHORITATIVE_FIELDS",
            ),
            "REJECT_CROSS_PORTFOLIO_LINKAGE": (
                {"scope_match": False},
                "REJECT",
                "DO_NOT_POPULATE_AUTHORITATIVE_FIELDS",
            ),
            "LINKAGE_REVIEW_REQUIRED": (
                {"scope_match": True},
                "APPEND",
                "REQUIRE_LINKAGE_REVIEW",
            ),
        }
        for row in self.outcomes:
            expected_condition, expected_storage, expected_canonical = expected_rows[row.outcome]
            actual_condition = row.condition.model_dump(exclude={"description"}, exclude_none=True)
            if actual_condition != expected_condition:
                raise ValueError(f"condition does not match outcome {row.outcome}")
            if row.storage_effect != expected_storage or row.canonical_effect != expected_canonical:
                raise ValueError(f"effects do not match outcome {row.outcome}")
        return self


class SourceOfTruthFieldRule(ContractModel):
    field_path: str = Field(min_length=1)
    owner: Literal[
        "EXECUTION_OR_TRADE_CAPTURE",
        "CONFIRMATION",
        "BOOKING_READ_BACK",
        "LINKAGE_RULE",
    ]
    trusted_sources: list[SourceSystem] = Field(min_length=1)
    source_precedence: list[SourceSystem] = Field(min_length=1)
    secondary_sources: list[SourceSystem] = Field(default_factory=list)
    conflict_outcome: Literal[
        "EMIT_BREAK_NO_SILENT_OVERWRITE",
        "RETAIN_SOURCE_ONLY",
        "REQUIRE_LINKAGE_REVIEW",
    ]

    @model_validator(mode="after")
    def trusted_sources_are_ordered(self) -> SourceOfTruthFieldRule:
        if len(set(self.source_precedence)) != len(self.source_precedence):
            raise ValueError("source_precedence must not contain duplicates")
        if not set(self.trusted_sources).issubset(self.source_precedence):
            raise ValueError("every trusted source must appear in source_precedence")
        return self


class SourceOfTruthPolicy(ContractModel):
    policy_version: SemanticVersion
    scope: Literal["MVP_SYNTHETIC_FX"]
    field_rules: list[SourceOfTruthFieldRule] = Field(min_length=1)

    @model_validator(mode="after")
    def fields_are_unique(self) -> SourceOfTruthPolicy:
        paths = [rule.field_path for rule in self.field_rules]
        if len(paths) != len(set(paths)):
            raise ValueError("source-of-truth field paths must be unique")
        expected_paths = set(_SOURCE_OF_TRUTH_FIELD_PATHS)
        actual_paths = set(paths)
        if actual_paths != expected_paths:
            missing = sorted(expected_paths - actual_paths)
            unexpected = sorted(actual_paths - expected_paths)
            raise ValueError(
                f"source-of-truth field paths must match the approved set; "
                f"missing={missing}, unexpected={unexpected}"
            )
        return self


# TS-4 deterministic trade-break taxonomy and lifecycle contracts.
BreakRuleVersion: TypeAlias = Literal["1.0.0"]
BreakFamily: TypeAlias = Literal[
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
]
BreakConditionCode: TypeAlias = Literal[
    "MISSING_SOURCE_AFTER_WATERMARK",
    "LINKAGE_CANDIDATE_SCOPE_INVARIANT",
    "DUPLICATE_SOURCE_IDENTITY_CONTENT",
    "EXACT_CURRENCY_PAIR_SIDE",
    "DECIMAL_OUTSIDE_TOLERANCE",
    "EXACT_TRADE_VALUE_DATE",
    "ALLOWED_LIFECYCLE_RELATION",
    "POST_ACTION_READBACK_RECONCILIATION",
]
BreakSeverity: TypeAlias = Literal["CRITICAL", "HIGH", "MEDIUM"]
BreakSeverityRule: TypeAlias = Literal[
    "FIXED",
    "MISSING_SOURCE_BY_OBSERVATION_KIND",
]
BreakEvidenceRole: TypeAlias = Literal[
    "INGESTION_WATERMARK",
    "EXPECTED_SOURCE",
    "CANDIDATE_LINK",
    "LINKAGE_DECISION",
    "SOURCE_PAYLOAD_PAIR",
    "SOURCE_METADATA",
    "FIELD_COMPARISON",
    "NORMALISATION_RULE",
    "DECIMAL_COMPARISON",
    "DATE_COMPARISON",
    "LIFECYCLE_RELATION",
    "ACTION_INSTRUCTION",
    "PRE_ACTION_READ",
    "POST_ACTION_READ",
    "CHANGED_FIELD_DIFF",
    "RECONCILIATION_RESULT",
    "DISPOSITION_APPROVAL",
]
BreakResolutionCode: TypeAlias = Literal[
    "SOURCE_OR_RECONCILIATION_PASS_OR_AUTHORISED_NON_ACTION",
    "LINKAGE_DECISION_AND_RECONCILIATION_PASS",
    "SOURCE_CORRECTION_OR_SUPERSESSION_AND_RECONCILIATION_PASS",
    "AGREEMENT_AFTER_CORRECTION_AND_RECONCILIATION_PASS",
    "WITHIN_TOLERANCE_AND_RECONCILIATION_PASS",
    "DATES_AGREE_AND_RECONCILIATION_PASS",
    "ALLOWED_LIFECYCLE_RELATION_AND_RECONCILIATION_PASS",
    "VERIFIED_READBACK_OR_AUTHORISED_NON_ACTION",
]
BreakLifecycleState: TypeAlias = Literal[
    "OPEN",
    "UNDER_INVESTIGATION",
    "RESOLUTION_PROPOSED",
    "ACTION_PENDING",
    "NO_ACTION_DISPOSITION_PENDING",
    "VERIFYING",
    "RESOLVED",
    "ESCALATED",
]
BreakTransitionReason: TypeAlias = Literal[
    "DETECTED",
    "BEGIN_INVESTIGATION",
    "PROPOSE_RESOLUTION",
    "ACTION_AUTHORISATION_PENDING",
    "NO_ACTION_DISPOSITION_PENDING",
    "BEGIN_VERIFICATION",
    "RESOLUTION_VERIFIED",
    "ESCALATE",
]
MaterialityBand: TypeAlias = Literal["MATERIAL", "UNASSESSED", "NON_MATERIAL"]
DeadlineStatus: TypeAlias = Literal["OVERDUE", "DUE", "NO_CONFIGURED_DEADLINE"]
PriorityTieBreaker: TypeAlias = Literal[
    "MATERIALITY_BAND",
    "SEVERITY",
    "LIFECYCLE_DEADLINE",
    "CASE_AGE",
]
BreakValueType: TypeAlias = Literal[
    "ABSENCE",
    "COUNT",
    "CURRENCY",
    "CURRENCY_PAIR",
    "SIDE",
    "DECIMAL",
    "DATE",
    "LIFECYCLE_STATUS",
    "IDENTIFIER",
    "READBACK",
    "SOURCE_IDENTITY",
    "SOURCE_VERSION",
    "CONTENT_HASH",
]
ToleranceMode: TypeAlias = Literal["NONE", "ABSOLUTE_DECIMAL", "RELATIVE_DECIMAL"]
BreakResolutionType: TypeAlias = Literal[
    "RECONCILIATION_PASS",
    "OWNER_APPROVED_NON_ACTION",
]
BreakFieldPath = Annotated[
    str,
    StringConstraints(pattern=r"^/(payload|linkage|source)/[a-z][a-z0-9_/-]*$"),
]
MissingSourceObservationKind: TypeAlias = Literal["EXECUTION", "CONFIRMATION", "BOOKING"]

_BREAK_FAMILY_ORDER: tuple[BreakFamily, ...] = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
)
BreakFamilyDefinitionSpec: TypeAlias = tuple[
    str,
    BreakConditionCode,
    BreakSeverity,
    BreakSeverityRule,
    tuple[BreakEvidenceRole, ...],
    BreakResolutionCode,
]
_BREAK_FAMILY_POLICY: dict[BreakFamily, BreakFamilyDefinitionSpec] = {
    "MISSING_REQUIRED_SOURCE": (
        "A required execution, confirmation or booking observation is absent after its "
        "configured deterministic arrival window.",
        "MISSING_SOURCE_AFTER_WATERMARK",
        "HIGH",
        "MISSING_SOURCE_BY_OBSERVATION_KIND",
        ("INGESTION_WATERMARK", "EXPECTED_SOURCE"),
        "SOURCE_OR_RECONCILIATION_PASS_OR_AUTHORISED_NON_ACTION",
    ),
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": (
        "An observation has zero or multiple eligible canonical-trade matches, or a proposed "
        "link crosses tenant or portfolio scope.",
        "LINKAGE_CANDIDATE_SCOPE_INVARIANT",
        "HIGH",
        "FIXED",
        ("CANDIDATE_LINK", "LINKAGE_DECISION"),
        "LINKAGE_DECISION_AND_RECONCILIATION_PASS",
    ),
    "DUPLICATE_SOURCE_CONFLICT": (
        "The same source business key and version has non-identical content, or two active "
        "records claim the same unique lifecycle identity.",
        "DUPLICATE_SOURCE_IDENTITY_CONTENT",
        "HIGH",
        "FIXED",
        ("SOURCE_PAYLOAD_PAIR", "SOURCE_METADATA"),
        "SOURCE_CORRECTION_OR_SUPERSESSION_AND_RECONCILIATION_PASS",
    ),
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": (
        "Normalised base and terms currencies or base-relative side disagree across required "
        "sources.",
        "EXACT_CURRENCY_PAIR_SIDE",
        "CRITICAL",
        "FIXED",
        ("FIELD_COMPARISON", "NORMALISATION_RULE"),
        "AGREEMENT_AFTER_CORRECTION_AND_RECONCILIATION_PASS",
    ),
    "ECONOMIC_VALUE_MISMATCH": (
        "Base amount, terms amount or quoted rate differs outside the approved field tolerance.",
        "DECIMAL_OUTSIDE_TOLERANCE",
        "CRITICAL",
        "FIXED",
        ("DECIMAL_COMPARISON", "NORMALISATION_RULE"),
        "WITHIN_TOLERANCE_AND_RECONCILIATION_PASS",
    ),
    "TRADE_OR_VALUE_DATE_MISMATCH": (
        "Trade date or value date differs across required sources after explicit calendar and "
        "normalisation rules.",
        "EXACT_TRADE_VALUE_DATE",
        "HIGH",
        "FIXED",
        ("DATE_COMPARISON", "NORMALISATION_RULE"),
        "DATES_AGREE_AND_RECONCILIATION_PASS",
    ),
    "LIFECYCLE_STATUS_MISMATCH": (
        "Confirmation, booking or reporting lifecycle status is inconsistent with the allowed "
        "state relation for the canonical trade.",
        "ALLOWED_LIFECYCLE_RELATION",
        "HIGH",
        "FIXED",
        ("LIFECYCLE_RELATION", "NORMALISATION_RULE"),
        "ALLOWED_LIFECYCLE_RELATION_AND_RECONCILIATION_PASS",
    ),
    "POST_ACTION_VERIFICATION_FAILURE": (
        "Read-back is unavailable, the target value or version differs, an unapproved field "
        "changed, or the original applicable break remains after action.",
        "POST_ACTION_READBACK_RECONCILIATION",
        "CRITICAL",
        "FIXED",
        (
            "ACTION_INSTRUCTION",
            "PRE_ACTION_READ",
            "POST_ACTION_READ",
            "CHANGED_FIELD_DIFF",
            "RECONCILIATION_RESULT",
        ),
        "VERIFIED_READBACK_OR_AUTHORISED_NON_ACTION",
    ),
}

BreakComparisonSpec: TypeAlias = tuple[str, BreakValueType]
_BREAK_COMPARISON_POLICY: dict[BreakFamily, tuple[BreakComparisonSpec, ...]] = {
    "MISSING_REQUIRED_SOURCE": (
        ("/source/execution_observation", "ABSENCE"),
        ("/source/confirmation_observation", "ABSENCE"),
        ("/source/booking_observation", "ABSENCE"),
    ),
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": (("/linkage/trade_id", "COUNT"),),
    "DUPLICATE_SOURCE_CONFLICT": (
        ("/source/source_business_key", "SOURCE_IDENTITY"),
        ("/source/source_version", "SOURCE_VERSION"),
        ("/source/content_hash", "CONTENT_HASH"),
    ),
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": (
        ("/payload/base_currency", "CURRENCY"),
        ("/payload/terms_currency", "CURRENCY"),
        ("/payload/side", "SIDE"),
    ),
    "ECONOMIC_VALUE_MISMATCH": (
        ("/payload/base_amount", "DECIMAL"),
        ("/payload/terms_amount", "DECIMAL"),
        ("/payload/quoted_rate", "DECIMAL"),
    ),
    "TRADE_OR_VALUE_DATE_MISMATCH": (
        ("/payload/trade_date", "DATE"),
        ("/payload/value_date", "DATE"),
    ),
    "LIFECYCLE_STATUS_MISMATCH": (("/payload/lifecycle_status", "LIFECYCLE_STATUS"),),
    "POST_ACTION_VERIFICATION_FAILURE": (
        ("/payload/book_id", "IDENTIFIER"),
        ("/payload/lifecycle_status", "LIFECYCLE_STATUS"),
    ),
}
_BREAK_COMPARISON_EVIDENCE_ROLES: dict[BreakFamily, frozenset[BreakEvidenceRole]] = {
    "MISSING_REQUIRED_SOURCE": frozenset({"EXPECTED_SOURCE"}),
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": frozenset({"CANDIDATE_LINK", "LINKAGE_DECISION"}),
    "DUPLICATE_SOURCE_CONFLICT": frozenset({"SOURCE_PAYLOAD_PAIR", "SOURCE_METADATA"}),
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": frozenset({"FIELD_COMPARISON", "NORMALISATION_RULE"}),
    "ECONOMIC_VALUE_MISMATCH": frozenset({"DECIMAL_COMPARISON", "NORMALISATION_RULE"}),
    "TRADE_OR_VALUE_DATE_MISMATCH": frozenset({"DATE_COMPARISON", "NORMALISATION_RULE"}),
    "LIFECYCLE_STATUS_MISMATCH": frozenset({"LIFECYCLE_RELATION", "NORMALISATION_RULE"}),
    "POST_ACTION_VERIFICATION_FAILURE": frozenset(
        {"ACTION_INSTRUCTION", "PRE_ACTION_READ", "POST_ACTION_READ", "CHANGED_FIELD_DIFF"}
    ),
}
_BREAK_ALLOWED_RESOLUTION_TYPES: dict[BreakFamily, frozenset[BreakResolutionType]] = {
    "MISSING_REQUIRED_SOURCE": frozenset({"RECONCILIATION_PASS", "OWNER_APPROVED_NON_ACTION"}),
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": frozenset({"RECONCILIATION_PASS"}),
    "DUPLICATE_SOURCE_CONFLICT": frozenset({"RECONCILIATION_PASS"}),
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": frozenset({"RECONCILIATION_PASS"}),
    "ECONOMIC_VALUE_MISMATCH": frozenset({"RECONCILIATION_PASS"}),
    "TRADE_OR_VALUE_DATE_MISMATCH": frozenset({"RECONCILIATION_PASS"}),
    "LIFECYCLE_STATUS_MISMATCH": frozenset({"RECONCILIATION_PASS"}),
    "POST_ACTION_VERIFICATION_FAILURE": frozenset(
        {"RECONCILIATION_PASS", "OWNER_APPROVED_NON_ACTION"}
    ),
}
_EXPECTED_FIELD_BY_MISSING_SOURCE_KIND: dict[MissingSourceObservationKind, str] = {
    "EXECUTION": "/source/execution_observation",
    "CONFIRMATION": "/source/confirmation_observation",
    "BOOKING": "/source/booking_observation",
}
_BREAK_ALLOWED_TOLERANCE_MODES: dict[BreakValueType, frozenset[ToleranceMode]] = {
    "ABSENCE": frozenset({"NONE"}),
    "COUNT": frozenset({"NONE"}),
    "CURRENCY": frozenset({"NONE"}),
    "CURRENCY_PAIR": frozenset({"NONE"}),
    "SIDE": frozenset({"NONE"}),
    "DECIMAL": frozenset({"NONE", "ABSOLUTE_DECIMAL", "RELATIVE_DECIMAL"}),
    "DATE": frozenset({"NONE"}),
    "LIFECYCLE_STATUS": frozenset({"NONE"}),
    "IDENTIFIER": frozenset({"NONE"}),
    "READBACK": frozenset({"NONE"}),
    "SOURCE_IDENTITY": frozenset({"NONE"}),
    "SOURCE_VERSION": frozenset({"NONE"}),
    "CONTENT_HASH": frozenset({"NONE"}),
}
_BREAK_DISTINCT_SOURCE_FAMILIES: frozenset[BreakFamily] = frozenset(
    {
        "CURRENCY_PAIR_OR_SIDE_MISMATCH",
        "DUPLICATE_SOURCE_CONFLICT",
        "ECONOMIC_VALUE_MISMATCH",
        "TRADE_OR_VALUE_DATE_MISMATCH",
        "LIFECYCLE_STATUS_MISMATCH",
    }
)

_BREAK_LIFECYCLE_STATES: tuple[BreakLifecycleState, ...] = (
    "OPEN",
    "UNDER_INVESTIGATION",
    "RESOLUTION_PROPOSED",
    "ACTION_PENDING",
    "NO_ACTION_DISPOSITION_PENDING",
    "VERIFYING",
    "RESOLVED",
    "ESCALATED",
)
_BREAK_TRANSITION_POLICY: tuple[
    tuple[BreakLifecycleState | None, BreakLifecycleState, BreakTransitionReason], ...
] = (
    (None, "OPEN", "DETECTED"),
    ("OPEN", "UNDER_INVESTIGATION", "BEGIN_INVESTIGATION"),
    ("OPEN", "ESCALATED", "ESCALATE"),
    ("UNDER_INVESTIGATION", "RESOLUTION_PROPOSED", "PROPOSE_RESOLUTION"),
    ("UNDER_INVESTIGATION", "ESCALATED", "ESCALATE"),
    ("RESOLUTION_PROPOSED", "ACTION_PENDING", "ACTION_AUTHORISATION_PENDING"),
    (
        "RESOLUTION_PROPOSED",
        "NO_ACTION_DISPOSITION_PENDING",
        "NO_ACTION_DISPOSITION_PENDING",
    ),
    ("RESOLUTION_PROPOSED", "ESCALATED", "ESCALATE"),
    ("ACTION_PENDING", "VERIFYING", "BEGIN_VERIFICATION"),
    ("ACTION_PENDING", "ESCALATED", "ESCALATE"),
    ("NO_ACTION_DISPOSITION_PENDING", "VERIFYING", "BEGIN_VERIFICATION"),
    ("NO_ACTION_DISPOSITION_PENDING", "ESCALATED", "ESCALATE"),
    ("VERIFYING", "RESOLVED", "RESOLUTION_VERIFIED"),
    ("VERIFYING", "ESCALATED", "ESCALATE"),
)


class BreakFamilyDefinition(ContractModel):
    family: BreakFamily
    definition: str = Field(min_length=1)
    condition_code: BreakConditionCode
    default_severity: BreakSeverity
    severity_rule: BreakSeverityRule
    required_evidence_roles: list[BreakEvidenceRole] = Field(min_length=1)
    resolution_code: BreakResolutionCode


class BreakPriorityPolicy(ContractModel):
    materiality_order: list[MaterialityBand]
    severity_order: list[BreakSeverity]
    deadline_order: list[DeadlineStatus]
    tie_breakers: list[PriorityTieBreaker]

    @model_validator(mode="after")
    def validate_ordering(self) -> BreakPriorityPolicy:
        if self.materiality_order != ["MATERIAL", "UNASSESSED", "NON_MATERIAL"]:
            raise ValueError("materiality_order must be MATERIAL, UNASSESSED, NON_MATERIAL")
        if self.severity_order != ["CRITICAL", "HIGH", "MEDIUM"]:
            raise ValueError("severity_order must be CRITICAL, HIGH, MEDIUM")
        if self.deadline_order != ["OVERDUE", "DUE", "NO_CONFIGURED_DEADLINE"]:
            raise ValueError("deadline_order must be OVERDUE, DUE, NO_CONFIGURED_DEADLINE")
        if self.tie_breakers != [
            "MATERIALITY_BAND",
            "SEVERITY",
            "LIFECYCLE_DEADLINE",
            "CASE_AGE",
        ]:
            raise ValueError("tie_breakers must follow the approved deterministic priority tuple")
        return self


class BreakLifecycleTransition(ContractModel):
    from_state: BreakLifecycleState | None
    to_state: BreakLifecycleState
    reason_code: BreakTransitionReason


class BreakTaxonomy(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    taxonomy_version: BreakRuleVersion
    scope: Literal["MVP_SYNTHETIC_FX"]
    families: list[BreakFamilyDefinition] = Field(min_length=8, max_length=8)
    lifecycle_states: list[BreakLifecycleState]
    allowed_transitions: list[BreakLifecycleTransition] = Field(min_length=14, max_length=14)
    priority_policy: BreakPriorityPolicy

    @model_validator(mode="after")
    def validate_taxonomy(self) -> BreakTaxonomy:
        if [definition.family for definition in self.families] != list(_BREAK_FAMILY_ORDER):
            raise ValueError("taxonomy must define the eight approved families in order")
        for definition in self.families:
            expected = _BREAK_FAMILY_POLICY[definition.family]
            actual = (
                definition.definition,
                definition.condition_code,
                definition.default_severity,
                definition.severity_rule,
                tuple(definition.required_evidence_roles),
                definition.resolution_code,
            )
            if actual != expected:
                raise ValueError(f"taxonomy definition for {definition.family} is not approved")
        if self.lifecycle_states != list(_BREAK_LIFECYCLE_STATES):
            raise ValueError("taxonomy lifecycle_states must match the approved state list")
        transitions = tuple(
            (transition.from_state, transition.to_state, transition.reason_code)
            for transition in self.allowed_transitions
        )
        if transitions != _BREAK_TRANSITION_POLICY:
            raise ValueError(
                "taxonomy allowed_transitions must match the approved transition matrix"
            )
        return self


class BreakSourceReference(ContractModel):
    source_observation_id: Identifier
    observation_kind: ObservationKind
    source_system: SourceSystem
    source_business_key: Identifier
    source_tenant_id: TenantId
    source_portfolio_id: PortfolioId
    source_version: SourceVersion
    content_hash: Sha256

    @model_validator(mode="after")
    def source_system_matches_kind(self) -> BreakSourceReference:
        expected_source_system = _SOURCE_SYSTEM_BY_OBSERVATION_KIND[self.observation_kind]
        if self.source_system != expected_source_system:
            raise ValueError("source_system must match observation_kind")
        return self


class BreakEvidence(ContractModel):
    evidence_id: Identifier
    role: BreakEvidenceRole
    content_hash: Sha256
    captured_at: AwareTimestamp
    source_observation_id: Identifier | None = None
    source_version: SourceVersion | None = None
    field_path: BreakFieldPath | None = None

    @model_validator(mode="after")
    def source_reference_is_complete(self) -> BreakEvidence:
        if self.source_observation_id is None and self.source_version is not None:
            raise ValueError("source_version requires source_observation_id")
        if self.source_observation_id is not None and self.source_version is None:
            raise ValueError("source_observation_id requires source_version")
        return self


class BreakTolerance(ContractModel):
    mode: ToleranceMode
    value: DecimalValue | None = None

    @model_validator(mode="after")
    def value_matches_mode(self) -> BreakTolerance:
        if self.mode == "NONE" and self.value is not None:
            raise ValueError("NONE tolerance must not carry a value")
        if self.mode != "NONE" and self.value is None:
            raise ValueError("decimal tolerance requires a value")
        return self


class BreakComparison(ContractModel):
    field_path: BreakFieldPath
    value_type: BreakValueType
    expected_value: str = Field(min_length=1)
    observed_value: str = Field(min_length=1)
    tolerance: BreakTolerance
    normalisation_rule_version: BreakRuleVersion
    evidence_ids: list[Identifier] = Field(min_length=1)
    expected_source_observation_id: Identifier | None
    expected_source_version: SourceVersion | None
    observed_source_observation_id: Identifier | None
    observed_source_version: SourceVersion | None

    @model_validator(mode="after")
    def comparison_values_match_type(self) -> BreakComparison:
        if self.expected_value == self.observed_value:
            raise ValueError("break comparisons must contain distinct expected and observed values")
        valid: bool
        if self.value_type == "COUNT":
            pattern = r"^[0-9]+$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "CURRENCY_PAIR":
            pattern = r"^[A-Z]{3}/[A-Z]{3}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "CURRENCY":
            pattern = r"^[A-Z]{3}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "SIDE":
            valid = self.expected_value in {"BUY_BASE", "SELL_BASE"} and self.observed_value in {
                "BUY_BASE",
                "SELL_BASE",
            }
        elif self.value_type == "DECIMAL":
            pattern = r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "DATE":
            try:
                date.fromisoformat(self.expected_value)
                date.fromisoformat(self.observed_value)
                valid = True
            except ValueError:
                valid = False
        elif self.value_type == "LIFECYCLE_STATUS":
            valid = self.expected_value in {
                "NEW",
                "CAPTURED",
                "CONFIRMED",
                "BOOKED",
                "AMENDED",
                "CANCELLED",
                "SETTLED",
            } and self.observed_value in {
                "NEW",
                "CAPTURED",
                "CONFIRMED",
                "BOOKED",
                "AMENDED",
                "CANCELLED",
                "SETTLED",
            }
        elif self.value_type == "SOURCE_IDENTITY":
            pattern = r"^[a-z][a-z0-9_-]{2,127}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "IDENTIFIER":
            pattern = r"^[a-z][a-z0-9_-]{2,127}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "SOURCE_VERSION":
            pattern = r"^[1-9][0-9]{0,18}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        elif self.value_type == "CONTENT_HASH":
            pattern = r"^sha256:[0-9a-f]{64}$"
            valid = bool(
                fullmatch(pattern, self.expected_value) and fullmatch(pattern, self.observed_value)
            )
        else:
            valid = True
        if not valid:
            raise ValueError(f"comparison values do not match value_type {self.value_type}")
        if self.tolerance.mode not in _BREAK_ALLOWED_TOLERANCE_MODES[self.value_type]:
            raise ValueError(f"{self.value_type} comparisons require an exact tolerance")
        return self


class BreakProductContext(ContractModel):
    product_type: ProductType
    settlement_rule_version: SettlementRuleVersion
    trade_date: date
    value_date: date

    @model_validator(mode="after")
    def settlement_window_is_valid(self) -> BreakProductContext:
        _validate_settlement_window(self.product_type, self.trade_date, self.value_date)
        return self


class DuplicateSourceConflict(ContractModel):
    conflict_type: Literal["SAME_SOURCE_KEY_VERSION_CONTENT"]
    source_observation_ids: list[Identifier] = Field(min_length=2)
    source_business_key: Identifier
    source_version: SourceVersion

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> DuplicateSourceConflict:
        if len(self.source_observation_ids) != len(set(self.source_observation_ids)):
            raise ValueError("duplicate source conflict observation IDs must be unique")
        return self


class ReconciliationSourceReference(ContractModel):
    source_observation_id: Identifier
    source_version: SourceVersion


class ReconciliationSourceValue(ReconciliationSourceReference):
    value: str = Field(min_length=1)


class ReconciliationPassComparison(ContractModel):
    field_path: BreakFieldPath
    value_type: BreakValueType
    values: list[ReconciliationSourceValue] = Field(min_length=1)

    @model_validator(mode="after")
    def source_values_are_unique(self) -> ReconciliationPassComparison:
        source_ids = [value.source_observation_id for value in self.values]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("reconciliation pass source values must use unique observations")
        return self


class ReconciliationPassProof(ContractModel):
    reconciliation_run_id: Identifier
    family: BreakFamily
    condition_code: BreakConditionCode
    predicate_code: BreakResolutionCode
    source_version_set: list[ReconciliationSourceReference] = Field(min_length=1)
    comparisons: list[ReconciliationPassComparison] = Field(min_length=1)

    @model_validator(mode="after")
    def proof_source_set_is_unique(self) -> ReconciliationPassProof:
        source_ids = [source.source_observation_id for source in self.source_version_set]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("reconciliation proof source_version_set must be unique")
        field_paths = [comparison.field_path for comparison in self.comparisons]
        if len(field_paths) != len(set(field_paths)):
            raise ValueError("reconciliation proof comparison field paths must be unique")
        return self


class BreakPriority(ContractModel):
    materiality_band: MaterialityBand
    deadline_status: DeadlineStatus
    case_age_seconds: int = Field(ge=0)
    ordering_key: tuple[int, int, int, int]


class MissingSourceExpectation(ContractModel):
    expected_observation_kind: MissingSourceObservationKind
    expected_source_system: SourceSystem
    field_path: BreakFieldPath
    arrival_window_rule_version: BreakRuleVersion
    watermark_at: AwareTimestamp
    expected_by: AwareTimestamp

    @model_validator(mode="after")
    def expectation_is_typed_and_ordered(self) -> MissingSourceExpectation:
        expected_system = _SOURCE_SYSTEM_BY_OBSERVATION_KIND[self.expected_observation_kind]
        if self.expected_source_system != expected_system:
            raise ValueError("expected_source_system must match expected_observation_kind")
        if (
            self.field_path
            != _EXPECTED_FIELD_BY_MISSING_SOURCE_KIND[self.expected_observation_kind]
        ):
            raise ValueError("missing-source field_path must match expected_observation_kind")
        if self.expected_by < self.watermark_at:
            raise ValueError("expected_by must not precede watermark_at")
        return self


class BreakResolution(ContractModel):
    resolution_type: BreakResolutionType
    reconciliation_run_id: Identifier | None = None
    disposition_id: Identifier | None = None
    approver: Actor | None = None
    evidence_ids: list[Identifier] = Field(min_length=1)
    evidence_roles: list[BreakEvidenceRole] = Field(min_length=1)
    reconciliation_proof: ReconciliationPassProof | None = None

    @model_validator(mode="after")
    def resolution_shape(self) -> BreakResolution:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("resolution evidence IDs must be unique")
        if len(self.evidence_roles) != len(set(self.evidence_roles)):
            raise ValueError("resolution evidence roles must be unique")
        if self.resolution_type == "RECONCILIATION_PASS":
            if self.reconciliation_run_id is None:
                raise ValueError("reconciliation-pass resolution requires reconciliation_run_id")
            if self.reconciliation_proof is None:
                raise ValueError("reconciliation-pass resolution requires reconciliation_proof")
            if self.disposition_id is not None or self.approver is not None:
                raise ValueError(
                    "reconciliation-pass resolution must not carry a disposition approver"
                )
        else:
            if self.disposition_id is None or self.approver is None:
                raise ValueError("non-action resolution requires disposition_id and approver")
            if self.approver.identity_type != "HUMAN":
                raise ValueError("non-action disposition must be approved by a human")
            if self.reconciliation_run_id is not None:
                raise ValueError("non-action resolution must not carry reconciliation_run_id")
            if self.reconciliation_proof is not None:
                raise ValueError("non-action resolution must not carry reconciliation_proof")
            if "DISPOSITION_APPROVAL" not in self.evidence_roles:
                raise ValueError("non-action resolution requires disposition evidence role")
        if (
            self.resolution_type == "RECONCILIATION_PASS"
            and "RECONCILIATION_RESULT" not in self.evidence_roles
        ):
            raise ValueError("reconciliation-pass resolution requires reconciliation evidence role")
        return self


_BREAK_SEVERITY_RANK: dict[BreakSeverity, int] = {
    "CRITICAL": 1,
    "HIGH": 2,
    "MEDIUM": 3,
}
_BREAK_MATERIALITY_RANK: dict[MaterialityBand, int] = {
    "MATERIAL": 1,
    "UNASSESSED": 2,
    "NON_MATERIAL": 3,
}
_BREAK_DEADLINE_RANK: dict[DeadlineStatus, int] = {
    "OVERDUE": 1,
    "DUE": 2,
    "NO_CONFIGURED_DEADLINE": 3,
}


def _expected_break_severity(
    family: BreakFamily,
    severity_context: ObservationKind | None,
) -> BreakSeverity:
    expected = _BREAK_FAMILY_POLICY[family]
    if expected[3] == "MISSING_SOURCE_BY_OBSERVATION_KIND":
        if severity_context not in {"EXECUTION", "CONFIRMATION", "BOOKING"}:
            raise ValueError(
                "missing-source breaks require EXECUTION, CONFIRMATION, or BOOKING context"
            )
        return "MEDIUM" if severity_context == "CONFIRMATION" else "HIGH"
    if severity_context is not None:
        raise ValueError("fixed-severity breaks must not carry severity_context")
    return expected[2]


def _reconciliation_values_agree(
    values: list[str],
    value_type: BreakValueType,
    tolerance: BreakTolerance,
) -> bool:
    """Check that a reconciliation proof demonstrates an accepted result."""

    if not values:
        return False
    if value_type == "DECIMAL":
        try:
            decimals = [Decimal(value) for value in values]
            if tolerance.mode == "NONE":
                return all(value == decimals[0] for value in decimals[1:])
            assert tolerance.value is not None
            allowed = Decimal(tolerance.value)
            if tolerance.mode == "ABSOLUTE_DECIMAL":
                return all(abs(value - decimals[0]) <= allowed for value in decimals[1:])
            return all(
                abs(value - decimals[0]) <= abs(decimals[0]) * allowed for value in decimals[1:]
            )
        except (InvalidOperation, ValueError):
            return False
    return all(value == values[0] for value in values[1:])


class TradeBreak(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    taxonomy_version: BreakRuleVersion
    detection_rule_version: BreakRuleVersion
    priority_rule_version: BreakRuleVersion
    lifecycle_rule_version: BreakRuleVersion
    break_id: Identifier
    break_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    trade_id: Identifier
    canonical_state_version: int = Field(ge=1)
    reconciliation_run_id: Identifier
    product_type: ProductType
    product_context: BreakProductContext
    family: BreakFamily
    condition_code: BreakConditionCode
    severity: BreakSeverity
    severity_context: ObservationKind | None = None
    priority: BreakPriority
    source_version_set: list[BreakSourceReference] = Field(min_length=1)
    evaluated_field_paths: list[BreakFieldPath] = Field(min_length=1)
    comparisons: list[BreakComparison] = Field(min_length=1)
    evidence: list[BreakEvidence] = Field(min_length=1)
    missing_source_expectation: MissingSourceExpectation | None = None
    duplicate_source_conflict: DuplicateSourceConflict | None = None
    state: BreakLifecycleState
    previous_state: BreakLifecycleState | None
    transition_reason: BreakTransitionReason
    detected_at: AwareTimestamp
    state_changed_at: AwareTimestamp
    resolved_at: AwareTimestamp | None = None
    resolution: BreakResolution | None = None
    supersedes_break_id: Identifier | None = None
    causal_label_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_break_invariants(self) -> TradeBreak:
        expected_definition = _BREAK_FAMILY_POLICY[self.family]
        if self.product_context.product_type != self.product_type:
            raise ValueError("product_context.product_type must match product_type")
        _validate_settlement_window(
            self.product_type,
            self.product_context.trade_date,
            self.product_context.value_date,
        )
        if self.condition_code != expected_definition[1]:
            raise ValueError("condition_code must match the selected break family")
        if self.severity != _expected_break_severity(self.family, self.severity_context):
            raise ValueError("severity must match the deterministic family severity rule")

        if self.family == "MISSING_REQUIRED_SOURCE":
            if self.missing_source_expectation is None:
                raise ValueError("missing-source breaks require a typed source expectation")
            if self.severity_context != self.missing_source_expectation.expected_observation_kind:
                raise ValueError("severity_context must match expected missing source kind")
            if self.detected_at < self.missing_source_expectation.expected_by:
                raise ValueError("missing-source break must be detected after its arrival window")
        elif self.missing_source_expectation is not None:
            raise ValueError("only missing-source breaks may carry a source expectation")

        source_ids = [source.source_observation_id for source in self.source_version_set]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_version_set observation IDs must be unique")
        for source in self.source_version_set:
            if (source.source_tenant_id, source.source_portfolio_id) != (
                self.tenant_id,
                self.portfolio_id,
            ):
                raise ValueError("source_version_set contains a source outside break scope")
        if self.family == "MISSING_REQUIRED_SOURCE":
            assert self.missing_source_expectation is not None
            if any(
                source.observation_kind == self.missing_source_expectation.expected_observation_kind
                for source in self.source_version_set
            ):
                raise ValueError("missing-source expectation must not have an available source")

        evaluated_paths = self.evaluated_field_paths
        if len(evaluated_paths) != len(set(evaluated_paths)):
            raise ValueError("evaluated_field_paths must be unique")
        comparison_paths = [comparison.field_path for comparison in self.comparisons]
        if len(comparison_paths) != len(set(comparison_paths)):
            raise ValueError("comparison field paths must be unique")
        if set(evaluated_paths) != set(comparison_paths):
            raise ValueError("evaluated_field_paths must match comparison field paths")

        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        indexed_evidence = {item.evidence_id: item for item in self.evidence}
        required_roles = set(expected_definition[4])
        actual_roles = {item.role for item in self.evidence}
        if not required_roles.issubset(actual_roles):
            missing_roles = sorted(required_roles - actual_roles)
            raise ValueError(f"evidence is missing required roles: {missing_roles}")
        if self.family == "MISSING_REQUIRED_SOURCE":
            assert self.missing_source_expectation is not None
            watermark_evidence = next(
                item for item in self.evidence if item.role == "INGESTION_WATERMARK"
            )
            expected_source_evidence = next(
                item for item in self.evidence if item.role == "EXPECTED_SOURCE"
            )
            if watermark_evidence.captured_at != self.missing_source_expectation.watermark_at:
                raise ValueError("ingestion watermark evidence must match watermark_at")
            if expected_source_evidence.field_path != self.missing_source_expectation.field_path:
                raise ValueError("expected source evidence must match the typed field_path")
        indexed_sources = {
            source.source_observation_id: source for source in self.source_version_set
        }
        if self.family == "DUPLICATE_SOURCE_CONFLICT":
            conflict = self.duplicate_source_conflict
            if conflict is None:
                raise ValueError("duplicate-source breaks require duplicate_source_conflict")
            if set(conflict.source_observation_ids) != set(indexed_sources):
                raise ValueError(
                    "duplicate-source conflict must identify every source in source_version_set"
                )
            conflict_sources = [
                indexed_sources[source_id] for source_id in conflict.source_observation_ids
            ]
            if any(
                source.source_business_key != conflict.source_business_key
                or source.source_version != conflict.source_version
                for source in conflict_sources
            ):
                raise ValueError(
                    "duplicate-source conflict must bind the declared source key and version"
                )
            if len({source.content_hash for source in conflict_sources}) < 2:
                raise ValueError(
                    "duplicate-source conflict requires non-identical source content hashes"
                )
        elif self.duplicate_source_conflict is not None:
            raise ValueError("only duplicate-source breaks may carry duplicate_source_conflict")
        for item in self.evidence:
            if item.source_observation_id is None:
                continue
            matched_source = indexed_sources.get(item.source_observation_id)
            if matched_source is None or item.source_version != matched_source.source_version:
                raise ValueError("evidence source reference must match source_version_set")

        allowed_comparisons = set(_BREAK_COMPARISON_POLICY[self.family])
        allowed_comparison_roles = _BREAK_COMPARISON_EVIDENCE_ROLES[self.family]
        for comparison in self.comparisons:
            if (comparison.field_path, comparison.value_type) not in allowed_comparisons:
                raise ValueError(
                    "comparison field_path and value_type must match the selected break family"
                )
            if len(comparison.evidence_ids) != len(set(comparison.evidence_ids)):
                raise ValueError("comparison evidence IDs must be unique")
            if self.family == "MISSING_REQUIRED_SOURCE":
                if (
                    comparison.expected_source_observation_id is not None
                    or comparison.expected_source_version is not None
                ):
                    raise ValueError("missing-source comparisons must not have an expected source")
                operand_ids = {comparison.observed_source_observation_id}
                if comparison.observed_source_observation_id is None:
                    raise ValueError("missing-source comparisons require an observed source")
            else:
                if (
                    comparison.expected_source_observation_id is None
                    or comparison.expected_source_version is None
                    or comparison.observed_source_observation_id is None
                    or comparison.observed_source_version is None
                ):
                    raise ValueError("comparisons require expected and observed source references")
                operand_ids = {
                    comparison.expected_source_observation_id,
                    comparison.observed_source_observation_id,
                }
            for source_id, source_version, operand_name in (
                (
                    comparison.expected_source_observation_id,
                    comparison.expected_source_version,
                    "expected",
                ),
                (
                    comparison.observed_source_observation_id,
                    comparison.observed_source_version,
                    "observed",
                ),
            ):
                if source_id is None or source_version is None:
                    if self.family == "MISSING_REQUIRED_SOURCE" and operand_name == "expected":
                        continue
                    raise ValueError(f"{operand_name} comparison source reference is incomplete")
                matched_operand = indexed_sources.get(source_id)
                if matched_operand is None or matched_operand.source_version != source_version:
                    raise ValueError(
                        f"{operand_name} comparison source reference must match source_version_set"
                    )
            if self.family in _BREAK_DISTINCT_SOURCE_FAMILIES and len(operand_ids) != 2:
                raise ValueError(
                    "this break family requires distinct expected and observed source observations"
                )
            for evidence_id in comparison.evidence_ids:
                if evidence_id not in indexed_evidence:
                    raise ValueError("comparison evidence IDs must reference break evidence")
            cited_source_ids = {
                indexed_evidence[evidence_id].source_observation_id
                for evidence_id in comparison.evidence_ids
                if indexed_evidence[evidence_id].source_observation_id is not None
            }
            if self.family != "MISSING_REQUIRED_SOURCE" and not operand_ids.issubset(
                cited_source_ids
            ):
                raise ValueError(
                    "comparison evidence must cite both expected and observed source observations"
                )
            for evidence_id in comparison.evidence_ids:
                item = indexed_evidence[evidence_id]
                if item.field_path != comparison.field_path:
                    raise ValueError("comparison evidence must bind to the comparison field_path")
                if item.role not in allowed_comparison_roles:
                    raise ValueError("comparison evidence role is not allowed for this family")
        for item in self.evidence:
            if item.field_path is not None and item.field_path not in set(evaluated_paths):
                raise ValueError("evidence field_path must be one of evaluated_field_paths")

        expected_ordering_key = (
            _BREAK_MATERIALITY_RANK[self.priority.materiality_band],
            _BREAK_SEVERITY_RANK[self.severity],
            _BREAK_DEADLINE_RANK[self.priority.deadline_status],
            -self.priority.case_age_seconds,
        )
        if self.priority.ordering_key != expected_ordering_key:
            raise ValueError("ordering_key must follow the deterministic priority tuple")

        transition = (self.previous_state, self.state, self.transition_reason)
        if transition not in _BREAK_TRANSITION_POLICY:
            raise ValueError("state transition is not permitted by the lifecycle matrix")
        if self.state_changed_at < self.detected_at:
            raise ValueError("state_changed_at must not precede detected_at")
        if self.break_version == 1 and self.supersedes_break_id is not None:
            raise ValueError("break version one must not supersede another break")
        if self.break_version > 1 and self.supersedes_break_id is None:
            raise ValueError("reopened break versions must reference the prior break")
        if self.break_version > 1 and self.supersedes_break_id == self.break_id:
            raise ValueError("reopened break versions must mint a new break_id")

        if self.state == "RESOLVED":
            if self.resolved_at is None or self.resolution is None:
                raise ValueError("resolved breaks require resolved_at and resolution evidence")
            if self.resolved_at < self.state_changed_at:
                raise ValueError("resolved_at must not precede state_changed_at")
            allowed_resolution_types = _BREAK_ALLOWED_RESOLUTION_TYPES[self.family]
            if self.resolution.resolution_type not in allowed_resolution_types:
                raise ValueError("resolution_type is not permitted by the selected break family")
            resolution_evidence = []
            for evidence_id in self.resolution.evidence_ids:
                if evidence_id not in indexed_evidence:
                    raise ValueError("resolution evidence IDs must reference break evidence")
                item = indexed_evidence[evidence_id]
                resolution_evidence.append(item)
            resolution_roles = {item.role for item in resolution_evidence}
            if resolution_roles != set(self.resolution.evidence_roles):
                raise ValueError("resolution evidence_roles must match cited evidence IDs")
            if self.resolution.resolution_type == "RECONCILIATION_PASS":
                if "RECONCILIATION_RESULT" not in resolution_roles:
                    raise ValueError(
                        "reconciliation-pass resolution requires reconciliation evidence"
                    )
                if self.resolution.reconciliation_run_id != self.reconciliation_run_id:
                    raise ValueError(
                        "reconciliation-pass resolution must reference the break reconciliation run"
                    )
                proof = self.resolution.reconciliation_proof
                if proof is None:
                    raise ValueError("reconciliation-pass resolution requires structured proof")
                if (
                    proof.reconciliation_run_id != self.reconciliation_run_id
                    or proof.family != self.family
                    or proof.condition_code != self.condition_code
                    or proof.predicate_code != expected_definition[5]
                ):
                    raise ValueError(
                        "reconciliation proof must bind to this break family, condition, "
                        "run, and predicate"
                    )
                expected_source_versions = {
                    source.source_observation_id: source.source_version
                    for source in self.source_version_set
                }
                proof_source_versions = {
                    source.source_observation_id: source.source_version
                    for source in proof.source_version_set
                }
                if proof_source_versions != expected_source_versions:
                    raise ValueError(
                        "reconciliation proof source_version_set must match the break source set"
                    )
                proof_by_path = {
                    comparison.field_path: comparison for comparison in proof.comparisons
                }
                if set(proof_by_path) != set(comparison_paths):
                    raise ValueError(
                        "reconciliation proof must cover exactly the break comparison fields"
                    )
                for comparison in self.comparisons:
                    proof_comparison = proof_by_path[comparison.field_path]
                    if proof_comparison.value_type != comparison.value_type:
                        raise ValueError(
                            "reconciliation proof value_type must match the break comparison"
                        )
                    proof_values = proof_comparison.values
                    proof_ids = {value.source_observation_id for value in proof_values}
                    if not proof_ids.issubset(expected_source_versions):
                        raise ValueError(
                            "reconciliation proof values must reference known break sources"
                        )
                    if any(
                        expected_source_versions[source_id] != value.source_version
                        for value in proof_values
                        for source_id in [value.source_observation_id]
                    ):
                        raise ValueError("reconciliation proof values must match source versions")
                    required_proof_ids = (
                        {comparison.observed_source_observation_id}
                        if self.family == "MISSING_REQUIRED_SOURCE"
                        else {
                            comparison.expected_source_observation_id,
                            comparison.observed_source_observation_id,
                        }
                    )
                    if not required_proof_ids.issubset(proof_ids):
                        raise ValueError(
                            "reconciliation proof must include the comparison operand sources"
                        )
                    if self.family in _BREAK_DISTINCT_SOURCE_FAMILIES and len(proof_ids) < 2:
                        raise ValueError(
                            "reconciliation proof must demonstrate both source observations"
                        )
                    if not _reconciliation_values_agree(
                        [value.value for value in proof_values],
                        comparison.value_type,
                        comparison.tolerance,
                    ):
                        raise ValueError(
                            "reconciliation proof values do not satisfy the comparison tolerance"
                        )
            elif "DISPOSITION_APPROVAL" not in resolution_roles:
                raise ValueError("non-action resolution requires disposition evidence")
            if any(self.resolved_at < item.captured_at for item in resolution_evidence):
                raise ValueError("resolved_at must follow every cited resolution evidence capture")
        elif self.resolved_at is not None or self.resolution is not None:
            raise ValueError("only resolved breaks may carry resolution fields")
        return self


ObservationModel: TypeAlias = (
    ExecutionObservation | TradeCaptureObservation | ConfirmationObservation | BookingObservation
)

_DOCUMENT_MODELS: dict[str, type[BaseModel]] = {
    "execution-observation": ExecutionObservation,
    "trade-capture-observation": TradeCaptureObservation,
    "confirmation-observation": ConfirmationObservation,
    "booking-observation": BookingObservation,
    "canonical-trade": CanonicalTrade,
    "canonical-trade-state": CanonicalTradeState,
    "linkage-decision": LinkageDecision,
    "identity-policy": IdentityPolicy,
    "source-of-truth-policy": SourceOfTruthPolicy,
    "break-taxonomy": BreakTaxonomy,
    "trade-break": TradeBreak,
}


def validate_contract_document(contract_name: str, document: Mapping[str, Any]) -> BaseModel:
    """Validate a document with its typed contract model.

    The function is intentionally pure: it parses and validates one document
    and performs no I/O or persistence.
    """

    model = _DOCUMENT_MODELS.get(contract_name)
    if model is None and contract_name in {"action-instruction", "evidence-item"}:
        # Import lazily so the TS-3/TS-4 model module remains the dependency
        # root for the later TS-5 action/evidence contracts.
        from .action_models import EvidenceItem, SignedActionInstruction

        if contract_name == "action-instruction":
            return SignedActionInstruction.model_validate(document)
        return EvidenceItem.model_validate(document)
    if model is None:
        raise ValueError(f"unsupported contract: {contract_name}")
    return model.model_validate(document)
