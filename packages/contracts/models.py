"""Pydantic v2 models for the TS-3 canonical FX contract.

The JSON Schemas in ``schemas/`` are the interchange contract. These models
add the cross-field rules that JSON Schema alone cannot express, while
remaining deliberately free of persistence, reconciliation, or transport
behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
SourceVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[1-9][0-9]{0,18}$"),
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
FieldPath = Annotated[
    str,
    StringConstraints(pattern=r"^/payload/[a-z][a-z0-9_/-]*$"),
]


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
        if self.value_date < self.trade_date:
            raise ValueError("value_date must not precede trade_date")
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
    event_time: datetime
    effective_time: datetime
    ingest_time: datetime
    source_sequence: int = Field(ge=0)
    lineage_group_id: Identifier
    actor: Actor
    supersedes_observation_id: Identifier | None = None
    supersession_reason: Literal["CORRECTION", "LATE_REVISION", "SOURCE_AMENDMENT"] | None = None
    payload: FxPayload

    @model_validator(mode="after")
    def validate_time_and_supersession(self) -> ObservationEnvelope:
        for field_name in ("event_time", "effective_time", "ingest_time"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone offset")
        if self.event_time > self.ingest_time:
            raise ValueError("event_time must not be after ingest_time")
        if self.supersedes_observation_id is None and self.supersession_reason is not None:
            raise ValueError("supersession_reason requires supersedes_observation_id")
        if self.supersedes_observation_id is not None and self.supersession_reason is None:
            raise ValueError("supersedes_observation_id requires supersession_reason")
        return self


class ExecutionPayload(FxPayload):
    execution_id: Identifier
    execution_type: Literal["NEW", "AMEND", "CANCEL"]
    execution_status: Literal["EXECUTED", "AMENDED", "CANCELLED"]
    execution_time: datetime
    order_id: Identifier | None = None


class ExecutionObservation(ObservationEnvelope):
    observation_kind: Literal["EXECUTION"] = "EXECUTION"
    source_system: Literal["FIX_EXECUTION"] = "FIX_EXECUTION"
    payload: ExecutionPayload


class TradeCapturePayload(FxPayload):
    capture_id: Identifier
    capture_type: Literal["NEW", "AMEND", "CANCEL"]
    capture_status: Literal["CAPTURED", "AMENDED", "CANCELLED"]
    capture_time: datetime
    execution_reference: Identifier


class TradeCaptureObservation(ObservationEnvelope):
    observation_kind: Literal["TRADE_CAPTURE"] = "TRADE_CAPTURE"
    source_system: Literal["FIX_TRADE_CAPTURE"] = "FIX_TRADE_CAPTURE"
    payload: TradeCapturePayload


class ConfirmationPayload(FxPayload):
    confirmation_id: Identifier
    confirmation_reference: str = Field(min_length=1, max_length=128)
    confirmation_status: Literal["PENDING", "AFFIRMED", "REJECTED", "AMENDED", "CANCELLED"]
    confirmation_time: datetime
    fpml_profile: Literal["fpml-style-fx-v1"]


class ConfirmationObservation(ObservationEnvelope):
    observation_kind: Literal["CONFIRMATION"] = "CONFIRMATION"
    source_system: Literal["FPML_CONFIRMATION"] = "FPML_CONFIRMATION"
    payload: ConfirmationPayload


class BookingPayload(FxPayload):
    booking_record_id: Identifier
    booking_version: int = Field(ge=1)
    booking_status: Literal["BOOKED", "AMENDED", "CANCELLED"]
    last_updated_time: datetime
    confirmation_reference: str | None = Field(default=None, max_length=128)
    record_fingerprint: Sha256


class BookingObservation(ObservationEnvelope):
    observation_kind: Literal["BOOKING"] = "BOOKING"
    source_system: Literal["MOCK_LEGACY_BOOKING"] = "MOCK_LEGACY_BOOKING"
    payload: BookingPayload


class CanonicalFields(ContractModel):
    product_type: ProductType
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
        if self.value_date < self.trade_date:
            raise ValueError("value_date must not precede trade_date")
        return self


class FieldProvenance(ContractModel):
    source_type: ObservationKind
    source_observation_id: Identifier
    source_observation_entity_version: int = Field(ge=1)
    source_version: SourceVersion
    field_path: FieldPath
    normalisation_rule_id: Identifier
    normalisation_rule_version: SemanticVersion
    resolution_rule_version: SemanticVersion
    observed_at: datetime
    effective_at: datetime
    ingested_at: datetime
    conflict_status: ConflictStatus


class FieldProvenanceMap(ContractModel):
    product_type: FieldProvenance
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


class CanonicalTrade(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    trade_id: Identifier
    entity_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    content_hash: Sha256
    created_at: datetime
    updated_at: datetime
    actor: Actor
    linkage_decision_id: Identifier
    state: CanonicalFields
    field_provenance: FieldProvenanceMap

    @model_validator(mode="after")
    def updated_after_created(self) -> CanonicalTrade:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class SourceVersionSetItem(ContractModel):
    observation_id: Identifier
    observation_kind: ObservationKind
    source_version: SourceVersion
    content_hash: Sha256


class CanonicalTradeState(ContractModel):
    schema_version: SchemaVersion = "1.0.0"
    trade_id: Identifier
    entity_version: int = Field(ge=1)
    canonical_state_version: int = Field(ge=1)
    tenant_id: TenantId
    portfolio_id: PortfolioId
    correlation_id: CorrelationId
    content_hash: Sha256
    as_of_time: datetime
    source_watermark: datetime
    source_version_set: list[SourceVersionSetItem] = Field(min_length=1)
    actor: Actor
    state: CanonicalFields
    field_provenance: FieldProvenanceMap


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
    created_at: datetime
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
        if self.decision == "ACCEPTED":
            if self.chosen_trade_id is None or len(self.candidate_links) != 1:
                raise ValueError("accepted linkage requires exactly one chosen candidate")
            candidate = self.candidate_links[0]
            if (candidate.tenant_id, candidate.portfolio_id) != (self.tenant_id, self.portfolio_id):
                raise ValueError("accepted linkage cannot cross tenant or portfolio scope")
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
            "source_version",
        ]
        if self.source_identity_fields != required_source:
            raise ValueError("source_identity_fields must match the approved identity tuple")
        if self.delivery_identity_fields != ["source_system", "source_event_id"]:
            raise ValueError("delivery_identity_fields must match the approved delivery tuple")
        precedences = [row.precedence for row in self.outcomes]
        if len(set(precedences)) != len(precedences) or set(precedences) != set(
            range(1, len(precedences) + 1)
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
}


def validate_contract_document(contract_name: str, document: Mapping[str, Any]) -> BaseModel:
    """Validate a document with its typed TS-3 contract model.

    The function is intentionally pure: it parses and validates one document
    and performs no I/O or persistence.
    """

    try:
        model = _DOCUMENT_MODELS[contract_name]
    except KeyError as exc:
        raise ValueError(f"unsupported TS-3 contract: {contract_name}") from exc
    return model.model_validate(document)
