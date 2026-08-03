"""Pure, seeded generation of the E3 synthetic FX lifecycle corpus.

The runtime source bundle and evaluator-only truth ledger are deliberately
different objects.  Source observations contain only the TS-3 observation
contracts; scenario causes, mutation labels, seeds, and expected differences
are kept in the truth ledger and never enter the runtime bundle.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from packages.contracts import compute_observation_content_hash, validate_contract_document

ProductType = Literal["FX_SPOT", "FX_FORWARD"]
ObservationKind = Literal["EXECUTION", "TRADE_CAPTURE", "CONFIRMATION", "BOOKING"]
BreakFamily = Literal[
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
]
CauseType = Literal[
    "SYNTHETIC_OPERATOR_ENTRY",
    "SYNTHETIC_MAPPING_TRANSFORMATION",
    "STALE_SOURCE_VERSION",
    "DUPLICATE_OR_REPLAY",
    "LATE_OR_REVISED_SOURCE",
    "UNKNOWN",
]

BREAK_FAMILIES: tuple[BreakFamily, ...] = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
)

PRODUCTS: tuple[ProductType, ...] = ("FX_SPOT", "FX_FORWARD")

_CONTRACT_BY_KIND: dict[ObservationKind, str] = {
    "EXECUTION": "execution-observation",
    "TRADE_CAPTURE": "trade-capture-observation",
    "CONFIRMATION": "confirmation-observation",
    "BOOKING": "booking-observation",
}

_SOURCE_SYSTEM_BY_KIND: dict[ObservationKind, str] = {
    "EXECUTION": "FIX_EXECUTION",
    "TRADE_CAPTURE": "FIX_TRADE_CAPTURE",
    "CONFIRMATION": "FPML_CONFIRMATION",
    "BOOKING": "MOCK_LEGACY_BOOKING",
}

_SOURCE_ACTOR_BY_KIND: dict[ObservationKind, str] = {
    "EXECUTION": "fix_execution",
    "TRADE_CAPTURE": "fix_trade_capture",
    "CONFIRMATION": "fpml_confirmation",
    "BOOKING": "mock_booking",
}

_SOURCE_SEQUENCE_BY_KIND: dict[ObservationKind, int] = {
    "EXECUTION": 1,
    "TRADE_CAPTURE": 2,
    "BOOKING": 3,
    "CONFIRMATION": 4,
}

_SOURCE_TIME_OFFSET_BY_KIND: dict[ObservationKind, timedelta] = {
    "EXECUTION": timedelta(0),
    "TRADE_CAPTURE": timedelta(minutes=1),
    "BOOKING": timedelta(minutes=2),
    "CONFIRMATION": timedelta(minutes=3),
}


@dataclass(frozen=True)
class ProductConfig:
    """Synthetic pair configuration, not a market-coverage assertion."""

    product_type: ProductType
    base_currency: str
    terms_currency: str
    value_days: int
    counterparty_id: str


_PRODUCT_CONFIG: dict[ProductType, ProductConfig] = {
    "FX_SPOT": ProductConfig("FX_SPOT", "EUR", "USD", 2, "cp_acme"),
    "FX_FORWARD": ProductConfig("FX_FORWARD", "USD", "JPY", 90, "cp_sakura"),
}


@dataclass(frozen=True)
class GeneratorConfig:
    """Stable generation parameters for the approved E3 population."""

    seed: int = 20260802
    tenant_id: str = "tenant_demo"
    portfolio_ids: tuple[str, ...] = ("portfolio_london", "portfolio_sydney")
    start_time: datetime = datetime(2026, 7, 1, tzinfo=UTC)
    generator_version: str = "1.0.0"
    calendar_version: str = "1.0.0"
    clean_per_product: int = 24
    mutations_per_family_product: int = 6

    def __post_init__(self) -> None:
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if not self.tenant_id.startswith("tenant_"):
            raise ValueError("tenant_id must use the TS-3 tenant namespace")
        if len(self.portfolio_ids) < 2:
            raise ValueError("E3 requires at least two portfolio scopes")
        if any(not portfolio.startswith("portfolio_") for portfolio in self.portfolio_ids):
            raise ValueError("portfolio_ids must use the TS-3 portfolio namespace")
        if self.start_time.tzinfo is None or self.start_time.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")
        if self.start_time.utcoffset() != timedelta(0):
            raise ValueError("start_time must already be UTC for deterministic generation")
        if self.clean_per_product != 24:
            raise ValueError("the approved E3 population requires 24 clean scenarios per product")
        if self.mutations_per_family_product != 6:
            raise ValueError("the approved E3 population requires 6 mutations per family/product")

    @property
    def scenario_count(self) -> int:
        return len(PRODUCTS) * (
            self.clean_per_product + len(BREAK_FAMILIES) * self.mutations_per_family_product
        )

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["portfolio_ids"] = list(self.portfolio_ids)
        values["start_time"] = _timestamp(self.start_time)
        return values


@dataclass(frozen=True)
class MutationSpec:
    """A single approved evaluator mutation template."""

    variant_id: str
    target_kind: ObservationKind
    field_path: str
    mutation_type: str
    late: bool = False
    out_of_order: bool = False


@dataclass(frozen=True)
class GeneratedCorpus:
    """The source fixtures plus evaluator-only deterministic evidence."""

    source_observations: tuple[dict[str, Any], ...]
    truth_ledger: tuple[dict[str, Any], ...]
    coverage_manifest: dict[str, Any]
    evidence_manifest: dict[str, Any]
    config: GeneratorConfig

    def runtime_bundle(self) -> dict[str, Any]:
        """Return only data that a runtime source-ingestion fixture may see."""

        return {
            "schema_version": "1.0.0",
            "source_contracts": [
                "execution-observation",
                "trade-capture-observation",
                "confirmation-observation",
                "booking-observation",
            ],
            "source_observations": copy.deepcopy(list(self.source_observations)),
        }

    def evaluator_bundle(self) -> dict[str, Any]:
        """Return the evaluator artefacts that are not part of runtime input."""

        return {
            "schema_version": "1.0.0",
            "scenario_truth_ledger": copy.deepcopy(list(self.truth_ledger)),
            "coverage_manifest": copy.deepcopy(self.coverage_manifest),
            "evidence_manifest": copy.deepcopy(self.evidence_manifest),
        }

    def write_to(self, directory: Path) -> None:
        """Write deterministic, machine-readable E3 artefacts."""

        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "source-fixtures.json", self.runtime_bundle())
        _write_json(
            directory / "scenario-truth-ledger.json",
            {
                "schema_version": "1.0.0",
                "access_classification": "EVALUATOR_ONLY",
                "scenarios": list(self.truth_ledger),
            },
        )
        _write_json(directory / "coverage-manifest.json", self.coverage_manifest)
        _write_json(directory / "evidence-manifest.json", self.evidence_manifest)


def generate_corpus(config: GeneratorConfig | None = None) -> GeneratedCorpus:
    """Generate and contract-validate the complete 144-scenario population."""

    active_config = config or GeneratorConfig()
    source_observations: list[dict[str, Any]] = []
    truth_ledger: list[dict[str, Any]] = []

    # The evaluator population is shuffled before runtime identifiers and
    # timestamps are allocated.  This keeps the approved counts while
    # preventing clean-before-mutated ordering from becoming a runtime label.
    plans: list[tuple[ProductType, MutationSpec | None]] = []
    for product in PRODUCTS:
        plans.extend((product, None) for _ in range(active_config.clean_per_product))
        for family in BREAK_FAMILIES:
            plans.extend((product, spec) for spec in _mutation_specs(family))
    random.Random(active_config.seed + 104729).shuffle(plans)

    for scenario_number, (product, spec) in enumerate(plans, start=1):
        base = _build_lifecycle(active_config, product, scenario_number)
        if spec is None:
            _validate_sources(base)
            source_observations.extend(base)
            truth_ledger.append(_clean_truth(active_config, product, scenario_number, base))
            continue

        mutated, facts, delivery, cause = _apply_mutation(
            base,
            active_config,
            product,
            scenario_number,
            spec,
        )
        _validate_sources(mutated)
        source_observations.extend(mutated)
        truth_ledger.append(
            _mutated_truth(
                active_config,
                product,
                scenario_number,
                spec,
                mutated,
                facts,
                delivery,
                cause,
            )
        )

    if scenario_number != active_config.scenario_count:
        raise AssertionError("generated scenario count does not match approved population")

    coverage = _coverage_manifest(active_config, truth_ledger, source_observations)
    evidence = _evidence_manifest(
        active_config,
        source_observations,
        truth_ledger,
        coverage,
    )
    return GeneratedCorpus(
        source_observations=tuple(source_observations),
        truth_ledger=tuple(truth_ledger),
        coverage_manifest=coverage,
        evidence_manifest=evidence,
        config=active_config,
    )


def _mutation_specs(family: BreakFamily) -> tuple[MutationSpec, ...]:
    if family == "MISSING_REQUIRED_SOURCE":
        return (
            MutationSpec(
                "missing_execution", "EXECUTION", "/source/execution_observation", "MISSING"
            ),
            MutationSpec(
                "missing_execution_late",
                "EXECUTION",
                "/source/execution_observation",
                "MISSING",
                late=True,
                out_of_order=True,
            ),
            MutationSpec(
                "missing_confirmation",
                "CONFIRMATION",
                "/source/confirmation_observation",
                "MISSING",
            ),
            MutationSpec(
                "missing_confirmation_late",
                "CONFIRMATION",
                "/source/confirmation_observation",
                "MISSING",
                late=True,
                out_of_order=True,
            ),
            MutationSpec("missing_booking", "BOOKING", "/source/booking_observation", "MISSING"),
            MutationSpec(
                "missing_booking_late",
                "BOOKING",
                "/source/booking_observation",
                "MISSING",
                late=True,
                out_of_order=True,
            ),
        )
    if family == "AMBIGUOUS_OR_UNMATCHED_LINKAGE":
        return (
            MutationSpec("unmatched_execution_key", "EXECUTION", "/linkage/trade_id", "UNMATCHED"),
            MutationSpec(
                "unmatched_capture_key", "TRADE_CAPTURE", "/linkage/trade_id", "UNMATCHED"
            ),
            MutationSpec(
                "unmatched_confirmation_key", "CONFIRMATION", "/linkage/trade_id", "UNMATCHED"
            ),
            MutationSpec(
                "ambiguous_execution_candidates",
                "EXECUTION",
                "/linkage/trade_id",
                "AMBIGUOUS",
                out_of_order=True,
            ),
            MutationSpec(
                "ambiguous_capture_candidates",
                "TRADE_CAPTURE",
                "/linkage/trade_id",
                "AMBIGUOUS",
                out_of_order=True,
            ),
            MutationSpec(
                "ambiguous_confirmation_candidates",
                "CONFIRMATION",
                "/linkage/trade_id",
                "AMBIGUOUS",
                out_of_order=True,
            ),
        )
    if family == "DUPLICATE_SOURCE_CONFLICT":
        return (
            MutationSpec("duplicate_execution", "EXECUTION", "/source/content_hash", "DUPLICATE"),
            MutationSpec(
                "duplicate_trade_capture", "TRADE_CAPTURE", "/source/content_hash", "DUPLICATE"
            ),
            MutationSpec(
                "duplicate_confirmation", "CONFIRMATION", "/source/content_hash", "DUPLICATE"
            ),
            MutationSpec("duplicate_booking", "BOOKING", "/source/content_hash", "DUPLICATE"),
            MutationSpec(
                "duplicate_execution_late_replay",
                "EXECUTION",
                "/source/content_hash",
                "DUPLICATE",
                late=True,
                out_of_order=True,
            ),
            MutationSpec(
                "duplicate_booking_late_replay",
                "BOOKING",
                "/source/content_hash",
                "DUPLICATE",
                late=True,
                out_of_order=True,
            ),
        )
    if family == "CURRENCY_PAIR_OR_SIDE_MISMATCH":
        return (
            MutationSpec(
                "execution_base_currency", "EXECUTION", "/payload/base_currency", "CURRENCY"
            ),
            MutationSpec(
                "capture_terms_currency", "TRADE_CAPTURE", "/payload/terms_currency", "CURRENCY"
            ),
            MutationSpec("confirmation_side", "CONFIRMATION", "/payload/side", "SIDE"),
            MutationSpec("booking_base_currency", "BOOKING", "/payload/base_currency", "CURRENCY"),
            MutationSpec(
                "execution_terms_currency", "EXECUTION", "/payload/terms_currency", "CURRENCY"
            ),
            MutationSpec("capture_side", "TRADE_CAPTURE", "/payload/side", "SIDE"),
        )
    if family == "ECONOMIC_VALUE_MISMATCH":
        return (
            MutationSpec("execution_base_amount", "EXECUTION", "/payload/base_amount", "DECIMAL"),
            MutationSpec(
                "capture_terms_amount", "TRADE_CAPTURE", "/payload/terms_amount", "DECIMAL"
            ),
            MutationSpec(
                "confirmation_quoted_rate", "CONFIRMATION", "/payload/quoted_rate", "DECIMAL"
            ),
            MutationSpec("booking_base_amount", "BOOKING", "/payload/base_amount", "DECIMAL"),
            MutationSpec("execution_terms_amount", "EXECUTION", "/payload/terms_amount", "DECIMAL"),
            MutationSpec("capture_quoted_rate", "TRADE_CAPTURE", "/payload/quoted_rate", "DECIMAL"),
        )
    if family == "TRADE_OR_VALUE_DATE_MISMATCH":
        return (
            MutationSpec("execution_trade_date", "EXECUTION", "/payload/trade_date", "DATE"),
            MutationSpec("capture_value_date", "TRADE_CAPTURE", "/payload/value_date", "DATE"),
            MutationSpec("confirmation_trade_date", "CONFIRMATION", "/payload/trade_date", "DATE"),
            MutationSpec("booking_value_date", "BOOKING", "/payload/value_date", "DATE"),
            MutationSpec("execution_value_date", "EXECUTION", "/payload/value_date", "DATE"),
            MutationSpec(
                "booking_trade_date_late_revision",
                "BOOKING",
                "/payload/trade_date",
                "DATE",
                late=True,
                out_of_order=True,
            ),
        )
    if family == "LIFECYCLE_STATUS_MISMATCH":
        return (
            MutationSpec(
                "execution_amend_status", "EXECUTION", "/payload/lifecycle_status", "AMEND"
            ),
            MutationSpec(
                "capture_amend_status", "TRADE_CAPTURE", "/payload/lifecycle_status", "AMEND"
            ),
            MutationSpec(
                "confirmation_amend_status", "CONFIRMATION", "/payload/lifecycle_status", "AMEND"
            ),
            MutationSpec(
                "execution_cancel_status", "EXECUTION", "/payload/lifecycle_status", "CANCEL"
            ),
            MutationSpec(
                "capture_cancel_status", "TRADE_CAPTURE", "/payload/lifecycle_status", "CANCEL"
            ),
            MutationSpec(
                "confirmation_cancel_status", "CONFIRMATION", "/payload/lifecycle_status", "CANCEL"
            ),
        )
    if family == "POST_ACTION_VERIFICATION_FAILURE":
        return (
            MutationSpec("booking_book_id_mismatch_1", "BOOKING", "/payload/book_id", "BOOK_ID"),
            MutationSpec(
                "booking_lifecycle_cancel_1", "BOOKING", "/payload/lifecycle_status", "CANCEL"
            ),
            MutationSpec("booking_book_id_mismatch_2", "BOOKING", "/payload/book_id", "BOOK_ID"),
            MutationSpec(
                "booking_lifecycle_cancel_2", "BOOKING", "/payload/lifecycle_status", "CANCEL"
            ),
            MutationSpec("booking_book_id_mismatch_3", "BOOKING", "/payload/book_id", "BOOK_ID"),
            MutationSpec(
                "booking_lifecycle_cancel_late",
                "BOOKING",
                "/payload/lifecycle_status",
                "CANCEL",
                late=True,
                out_of_order=True,
            ),
        )
    raise AssertionError(f"unsupported approved break family: {family}")


def _build_lifecycle(
    config: GeneratorConfig,
    product: ProductType,
    scenario_number: int,
) -> list[dict[str, Any]]:
    product_config = _PRODUCT_CONFIG[product]
    portfolio_id = config.portfolio_ids[(scenario_number - 1) % len(config.portfolio_ids)]
    trade_id = _opaque_identifier(config, "trade", "lifecycle", scenario_number)
    correlation_id = _opaque_identifier(config, "corr", "lifecycle", scenario_number)
    lineage_id = _opaque_identifier(config, "lineage", "lifecycle", scenario_number)
    counterparty = _opaque_identifier(config, "counterparty", product_config.product_type)
    book = _opaque_identifier(config, "book", "portfolio", portfolio_id)
    rng = random.Random(config.seed + scenario_number * 7919)
    base_amount = Decimal("1000000.00") + Decimal(rng.randrange(0, 25)) * Decimal("1000.00")
    if product == "FX_SPOT":
        rate = Decimal("1.0800") + Decimal(rng.randrange(0, 100)) / Decimal("10000")
    else:
        base_amount = Decimal("2500000.00") + Decimal(rng.randrange(0, 25)) * Decimal("10000.00")
        rate = Decimal("153.0000") + Decimal(rng.randrange(0, 1000)) / Decimal("1000")
    terms_amount = (base_amount * rate).quantize(Decimal("0.01"))
    trade_date = (config.start_time + timedelta(days=(scenario_number - 1) // 8)).date()
    value_date = trade_date + timedelta(days=product_config.value_days)
    base_time = config.start_time + timedelta(minutes=scenario_number * 7)
    common_payload: dict[str, Any] = {
        "product_type": product,
        "settlement_rule_version": "1.0.0",
        "source_trade_id": trade_id,
        "base_currency": product_config.base_currency,
        "terms_currency": product_config.terms_currency,
        "side": "BUY_BASE" if rng.randrange(2) == 0 else "SELL_BASE",
        "base_amount": {
            "currency": product_config.base_currency,
            "value": _decimal_string(base_amount, 2),
            "scale": 2,
        },
        "terms_amount": {
            "currency": product_config.terms_currency,
            "value": _decimal_string(terms_amount, 2),
            "scale": 2,
        },
        "quoted_rate": {
            "value": _decimal_string(rate, 4),
            "scale": 4,
            "orientation": "TERMS_CURRENCY_PER_BASE_CURRENCY",
        },
        "trade_date": trade_date.isoformat(),
        "value_date": value_date.isoformat(),
        "counterparty_id": counterparty,
        "book_id": book,
    }
    observations: list[dict[str, Any]] = []
    for kind in ("EXECUTION", "TRADE_CAPTURE", "BOOKING", "CONFIRMATION"):
        offset = _SOURCE_TIME_OFFSET_BY_KIND[kind]
        event_time = base_time + offset
        effective_time = event_time + timedelta(seconds=1)
        ingest_time = event_time + timedelta(seconds=3)
        payload = copy.deepcopy(common_payload)
        payload.update(_kind_payload(kind, correlation_id, event_time))
        observation = {
            "schema_version": "1.0.0",
            "observation_id": _opaque_identifier(
                config, f"obs_{kind.lower()}", "base", scenario_number, kind
            ),
            "observation_kind": kind,
            "entity_version": 1,
            "tenant_id": config.tenant_id,
            "portfolio_id": portfolio_id,
            "correlation_id": correlation_id,
            "source_system": _SOURCE_SYSTEM_BY_KIND[kind],
            "source_event_id": _opaque_identifier(config, "evt", "base", scenario_number, kind),
            "source_business_key": trade_id,
            "source_version": "1",
            "content_hash": "sha256:" + "0" * 64,
            "event_time": _timestamp(event_time),
            "effective_time": _timestamp(effective_time),
            "ingest_time": _timestamp(ingest_time),
            "source_sequence": _SOURCE_SEQUENCE_BY_KIND[kind],
            "lineage_group_id": lineage_id,
            "actor": {"identity_type": "SOURCE", "actor_id": _SOURCE_ACTOR_BY_KIND[kind]},
            "payload": payload,
        }
        _recompute_hashes(observation)
        observations.append(observation)
    return observations


def _kind_payload(
    kind: ObservationKind,
    correlation_id: str,
    event_time: datetime,
) -> dict[str, Any]:
    suffix = correlation_id.removeprefix("corr_")
    if kind == "EXECUTION":
        return {
            "lifecycle_status": "NEW",
            "execution_id": f"exec_{suffix}",
            "execution_type": "NEW",
            "execution_status": "EXECUTED",
            "execution_time": _timestamp(event_time),
            "order_id": f"order_{suffix}",
        }
    if kind == "TRADE_CAPTURE":
        return {
            "lifecycle_status": "CAPTURED",
            "capture_id": f"capture_{suffix}",
            "capture_type": "NEW",
            "capture_status": "CAPTURED",
            "capture_time": _timestamp(event_time),
            "execution_reference": f"exec_{suffix}",
        }
    if kind == "CONFIRMATION":
        return {
            "lifecycle_status": "CONFIRMED",
            "confirmation_id": f"confirmation_{suffix}",
            "confirmation_reference": f"CONF-{suffix.upper()}",
            "confirmation_status": "AFFIRMED",
            "confirmation_time": _timestamp(event_time),
            "fpml_profile": "fpml-style-fx-v1",
        }
    if kind == "BOOKING":
        payload = {
            "lifecycle_status": "BOOKED",
            "booking_record_id": f"booking_{suffix}",
            "booking_version": 1,
            "booking_status": "BOOKED",
            "last_updated_time": _timestamp(event_time),
            "confirmation_reference": f"CONF-{suffix.upper()}",
            "record_fingerprint": "sha256:" + "0" * 64,
        }
        return payload
    raise AssertionError(f"unsupported observation kind: {kind}")


def _apply_mutation(
    base: list[dict[str, Any]],
    config: GeneratorConfig,
    product: ProductType,
    scenario_number: int,
    spec: MutationSpec,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], CauseType]:
    mutated = copy.deepcopy(base)
    target = _find_observation(mutated, spec.target_kind)
    base_target = copy.deepcopy(target) if target is not None else None
    facts: list[dict[str, Any]]
    cause: CauseType
    delivery = ["CONFLICTING"]

    if spec.mutation_type == "MISSING":
        if target is None:
            raise AssertionError("missing mutation target was not present")
        mutated = [observation for observation in mutated if observation is not target]
        facts = [
            {
                "path": spec.field_path,
                "value_type": "ABSENCE",
                "expected": {"observation_kind": spec.target_kind},
                "observed": None,
            }
        ]
        cause = "LATE_OR_REVISED_SOURCE" if spec.late else "SYNTHETIC_OPERATOR_ENTRY"
        delivery = ["MISSING"]
    elif spec.mutation_type == "UNMATCHED":
        if target is None or base_target is None:
            raise AssertionError("unmatched mutation target was not present")
        alternate_trade_id = _opaque_identifier(
            config, "trade", "alternate", scenario_number, spec.variant_id
        )
        target["source_business_key"] = alternate_trade_id
        target["payload"]["source_trade_id"] = alternate_trade_id
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "COUNT",
                "expected": 1,
                "observed": 0,
                "source_observation_id": base_target["observation_id"],
            }
        ]
        cause = "SYNTHETIC_MAPPING_TRANSFORMATION"
        delivery = ["UNMATCHED"]
    elif spec.mutation_type == "AMBIGUOUS":
        if target is None or base_target is None:
            raise AssertionError("ambiguous mutation target was not present")
        candidate = copy.deepcopy(target)
        candidate_suffix = _opaque_token(
            config, "candidate", scenario_number, spec.target_kind, spec.variant_id
        )
        candidate["observation_id"] = _opaque_identifier(
            config, f"obs_{spec.target_kind.lower()}", "alternate", scenario_number, spec.variant_id
        )
        candidate["source_event_id"] = _opaque_identifier(
            config, "evt", "alternate", scenario_number, spec.variant_id
        )
        candidate["source_version"] = "2"
        candidate["payload"]["source_trade_id"] = _opaque_identifier(
            config, "trade", "alternate", scenario_number, spec.variant_id
        )
        _rename_payload_identity(candidate, spec.target_kind, candidate_suffix)
        _recompute_hashes(candidate)
        mutated.append(candidate)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "COUNT",
                "expected": 1,
                "observed": 2,
                "source_observation_id": base_target["observation_id"],
                "candidate_observation_id": candidate["observation_id"],
            }
        ]
        cause = "SYNTHETIC_MAPPING_TRANSFORMATION"
        delivery = ["CONFLICTING", "OUT_OF_ORDER"]
    elif spec.mutation_type == "DUPLICATE":
        if target is None or base_target is None:
            raise AssertionError("duplicate mutation target was not present")
        duplicate = copy.deepcopy(target)
        duplicate["observation_id"] = _opaque_identifier(
            config, f"obs_{spec.target_kind.lower()}", "alternate", scenario_number, spec.variant_id
        )
        duplicate["source_event_id"] = _opaque_identifier(
            config, "evt", "alternate", scenario_number, spec.variant_id
        )
        # A delivery-only identifier change is a legitimate idempotent replay
        # under canonical observation hashing.  The conflict corpus must alter
        # actual source content while retaining the same source identity and
        # version, so change a kind-specific payload identity that is not a
        # canonical economics field.
        identity_field, identity_prefix = {
            "EXECUTION": ("execution_id", "exec"),
            "TRADE_CAPTURE": ("capture_id", "capture"),
            "CONFIRMATION": ("confirmation_id", "confirmation"),
            "BOOKING": ("booking_record_id", "booking"),
        }[spec.target_kind]
        duplicate["payload"][identity_field] = _opaque_identifier(
            config,
            identity_prefix,
            "conflict",
            scenario_number,
            spec.variant_id,
        )
        if spec.late:
            duplicate["ingest_time"] = _timestamp(
                _parse_timestamp(duplicate["ingest_time"]) + timedelta(minutes=5)
            )
        _recompute_hashes(duplicate)
        mutated.append(duplicate)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "CONTENT_HASH",
                "expected": base_target["content_hash"],
                "observed": duplicate["content_hash"],
                "expected_source_observation_id": base_target["observation_id"],
                "observed_source_observation_id": duplicate["observation_id"],
            }
        ]
        cause = "DUPLICATE_OR_REPLAY"
        delivery = ["DUPLICATE", "LATE" if spec.late else "REPLAY"]
    elif spec.mutation_type in {"CURRENCY", "SIDE"}:
        if target is None or base_target is None:
            raise AssertionError("currency/side mutation target was not present")
        before = _path_value(base_target, spec.field_path)
        if spec.mutation_type == "SIDE":
            target["payload"]["side"] = (
                "SELL_BASE" if target["payload"]["side"] == "BUY_BASE" else "BUY_BASE"
            )
        elif spec.field_path.endswith("base_currency"):
            target["payload"]["base_currency"] = "GBP"
            target["payload"]["base_amount"]["currency"] = "GBP"
        else:
            target["payload"]["terms_currency"] = "CHF"
            target["payload"]["terms_amount"]["currency"] = "CHF"
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": spec.mutation_type,
                "expected": before,
                "observed": _path_value(target, spec.field_path),
                "source_observation_id": target["observation_id"],
            }
        ]
        cause = "SYNTHETIC_OPERATOR_ENTRY"
    elif spec.mutation_type == "DECIMAL":
        if target is None or base_target is None:
            raise AssertionError("economic mutation target was not present")
        before = _path_value(base_target, spec.field_path)
        if spec.field_path.endswith("base_amount"):
            amount = Decimal(target["payload"]["base_amount"]["value"]) + Decimal("1000.00")
            target["payload"]["base_amount"]["value"] = _decimal_string(amount, 2)
        elif spec.field_path.endswith("terms_amount"):
            amount = Decimal(target["payload"]["terms_amount"]["value"]) + Decimal("1.00")
            target["payload"]["terms_amount"]["value"] = _decimal_string(amount, 2)
        else:
            rate = Decimal(target["payload"]["quoted_rate"]["value"]) + Decimal("0.0100")
            target["payload"]["quoted_rate"]["value"] = _decimal_string(rate, 4)
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "DECIMAL",
                "expected": before,
                "observed": _path_value(target, spec.field_path),
                "source_observation_id": target["observation_id"],
            }
        ]
        cause = "SYNTHETIC_OPERATOR_ENTRY"
    elif spec.mutation_type == "DATE":
        if target is None or base_target is None:
            raise AssertionError("date mutation target was not present")
        before = _path_value(base_target, spec.field_path)
        product_config = _PRODUCT_CONFIG[product]
        trade = date.fromisoformat(target["payload"]["trade_date"])
        if spec.field_path.endswith("trade_date"):
            new_date = trade + timedelta(days=1)
        elif product_config.product_type == "FX_SPOT":
            new_date = trade + timedelta(days=3)
        else:
            new_date = trade + timedelta(days=91)
        target["payload"][spec.field_path.rsplit("/", 1)[1]] = new_date.isoformat()
        if spec.late:
            target["ingest_time"] = _timestamp(
                _parse_timestamp(target["ingest_time"]) + timedelta(minutes=5)
            )
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "DATE",
                "expected": before,
                "observed": _path_value(target, spec.field_path),
                "source_observation_id": target["observation_id"],
            }
        ]
        cause = "LATE_OR_REVISED_SOURCE" if spec.late else "SYNTHETIC_OPERATOR_ENTRY"
    elif spec.mutation_type in {"AMEND", "CANCEL"}:
        if target is None or base_target is None:
            raise AssertionError("lifecycle mutation target was not present")
        status = "AMENDED" if spec.mutation_type == "AMEND" else "CANCELLED"
        _set_lifecycle(target, spec.target_kind, status, spec.mutation_type)
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "LIFECYCLE_STATUS",
                "expected": base_target["payload"]["lifecycle_status"],
                "observed": target["payload"]["lifecycle_status"],
                "source_observation_id": target["observation_id"],
            }
        ]
        cause = (
            "STALE_SOURCE_VERSION" if spec.mutation_type == "AMEND" else "SYNTHETIC_OPERATOR_ENTRY"
        )
    elif spec.mutation_type == "BOOK_ID":
        if target is None or base_target is None:
            raise AssertionError("post-action mutation target was not present")
        before = _path_value(base_target, spec.field_path)
        target["payload"]["book_id"] = _opaque_identifier(
            config, "book", "post-action", scenario_number, spec.variant_id
        )
        _recompute_hashes(target)
        facts = [
            {
                "path": spec.field_path,
                "value_type": "IDENTIFIER",
                "expected": before,
                "observed": _path_value(target, spec.field_path),
                "source_observation_id": target["observation_id"],
            }
        ]
        cause = "SYNTHETIC_MAPPING_TRANSFORMATION"
    elif spec.mutation_type == "CANCEL":
        raise AssertionError("unreachable duplicate lifecycle branch")
    else:
        raise AssertionError(f"unsupported mutation type: {spec.mutation_type}")

    if spec.late and spec.mutation_type not in {"DUPLICATE", "DATE"}:
        if target is not None and target in mutated:
            target["ingest_time"] = _timestamp(
                _parse_timestamp(target["ingest_time"]) + timedelta(minutes=5)
            )
            _recompute_hashes(target)
            delivery.append("LATE")
    if spec.out_of_order:
        if target is not None and target in mutated:
            mutated.remove(target)
            mutated.append(target)
        if "OUT_OF_ORDER" not in delivery:
            delivery.append("OUT_OF_ORDER")
    return mutated, facts, delivery, cause


def _clean_truth(
    config: GeneratorConfig,
    product: ProductType,
    scenario_number: int,
    source: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "scenario_id": f"scenario_{scenario_number:03d}",
        "lineage_group_id": source[0]["lineage_group_id"],
        "tenant_id": config.tenant_id,
        "portfolio_id": source[0]["portfolio_id"],
        "product_type": product,
        "population": "CLEAN",
        "break_family": None,
        "variant_id": "clean_baseline",
        "cause_type": None,
        "source_mutation": {"mutation_type": "NONE"},
        "delivery_behaviour": ["IN_ORDER"],
        "expected_difference_facts": [],
        "seed": config.seed,
        "source_observation_ids": [item["observation_id"] for item in source],
        "provenance_graph": _provenance_graph(source),
        "truth_access_classification": "EVALUATOR_ONLY",
    }


def _mutated_truth(
    config: GeneratorConfig,
    product: ProductType,
    scenario_number: int,
    spec: MutationSpec,
    source: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    delivery: list[str],
    cause: CauseType,
) -> dict[str, Any]:
    family = _family_for_spec(spec)
    source_mutation = {
        "mutation_type": spec.mutation_type,
        "target_observation_kind": spec.target_kind,
        "field_path": spec.field_path,
    }
    return {
        "schema_version": "1.0.0",
        "scenario_id": f"scenario_{scenario_number:03d}",
        "lineage_group_id": source[0]["lineage_group_id"],
        "tenant_id": config.tenant_id,
        "portfolio_id": source[0]["portfolio_id"],
        "product_type": product,
        "population": "MUTATED",
        "break_family": family,
        "variant_id": spec.variant_id,
        "cause_type": cause,
        "source_mutation": source_mutation,
        "delivery_behaviour": sorted(set(delivery)),
        "expected_difference_facts": facts,
        "seed": config.seed,
        "source_observation_ids": [item["observation_id"] for item in source],
        "provenance_graph": _provenance_graph(
            source,
            cause_type=cause,
            source_mutation=source_mutation,
            delivery_behaviour=sorted(set(delivery)),
            expected_difference_facts=facts,
            break_family=family,
        ),
        "truth_access_classification": "EVALUATOR_ONLY",
    }


def _family_for_spec(spec: MutationSpec) -> BreakFamily:
    for family in BREAK_FAMILIES:
        if spec in _mutation_specs(family):
            return family
    raise AssertionError(f"mutation spec is not in the approved taxonomy: {spec.variant_id}")


def _provenance_graph(
    source: list[dict[str, Any]],
    *,
    cause_type: CauseType | None = None,
    source_mutation: dict[str, Any] | None = None,
    delivery_behaviour: list[str] | None = None,
    expected_difference_facts: list[dict[str, Any]] | None = None,
    break_family: BreakFamily | None = None,
) -> dict[str, Any]:
    """Build the evaluator-only cause-to-break provenance graph.

    The scalar truth fields remain convenient for filtering, while this graph
    explicitly preserves the ADR-006 chain for independent oracle checks.
    Runtime source observations never include these nodes or edges.
    """

    if not source:
        raise ValueError("a provenance graph requires at least one source observation")

    lineage_id = source[0]["lineage_group_id"]
    nodes: list[dict[str, Any]] = [
        {"node_id": f"lineage:{lineage_id}", "node_type": "LINEAGE"},
        *[
            {
                "node_id": f"observation:{item['observation_id']}",
                "node_type": "SOURCE_OBSERVATION",
                "observation_id": item["observation_id"],
                "observation_kind": item["observation_kind"],
                "source_system": item["source_system"],
            }
            for item in source
        ],
    ]
    edges: list[dict[str, str]] = [
        {
            "from": f"observation:{item['observation_id']}",
            "to": f"lineage:{lineage_id}",
            "relationship": "BELONGS_TO_LINEAGE",
        }
        for item in source
    ]

    if cause_type is None:
        return {"nodes": nodes, "edges": edges}
    if (
        source_mutation is None
        or delivery_behaviour is None
        or expected_difference_facts is None
        or break_family is None
    ):
        raise ValueError("mutated provenance requires the complete truth chain")

    cause_node = "cause:0"
    mutation_node = "source_mutation:0"
    break_node = "break_family:0"
    target_kind = source_mutation["target_observation_kind"]
    target_observations = [item for item in source if item["observation_kind"] == target_kind]
    nodes.extend(
        [
            {"node_id": cause_node, "node_type": "SYNTHETIC_CAUSE", "cause_type": cause_type},
            {
                "node_id": mutation_node,
                "node_type": "SOURCE_MUTATION",
                **copy.deepcopy(source_mutation),
            },
            {
                "node_id": break_node,
                "node_type": "BREAK_FAMILY",
                "break_family": break_family,
            },
        ]
    )
    edges.append({"from": cause_node, "to": mutation_node, "relationship": "CAUSE_OF"})

    for index, behaviour in enumerate(delivery_behaviour):
        delivery_node = f"delivery:{index}"
        nodes.append(
            {
                "node_id": delivery_node,
                "node_type": "DELIVERY_BEHAVIOUR",
                "behaviour": behaviour,
            }
        )
        edges.append(
            {
                "from": mutation_node,
                "to": delivery_node,
                "relationship": "DELIVERED_AS",
            }
        )
        if behaviour == "MISSING":
            expected_node = f"expected_source_absence:{target_kind}"
            if not any(node["node_id"] == expected_node for node in nodes):
                nodes.append(
                    {
                        "node_id": expected_node,
                        "node_type": "EXPECTED_SOURCE_ABSENCE",
                        "observation_kind": target_kind,
                    }
                )
            edges.extend(
                [
                    {
                        "from": mutation_node,
                        "to": expected_node,
                        "relationship": "EXPECTS_ABSENCE",
                    },
                    {
                        "from": delivery_node,
                        "to": expected_node,
                        "relationship": "DELIVERS_ABSENCE",
                    },
                ]
            )
        else:
            edges.extend(
                {
                    "from": delivery_node,
                    "to": f"observation:{item['observation_id']}",
                    "relationship": "DELIVERS_OBSERVATION",
                }
                for item in source
            )

    for item in target_observations:
        edges.append(
            {
                "from": mutation_node,
                "to": f"observation:{item['observation_id']}",
                "relationship": "MUTATES_OBSERVATION",
            }
        )

    source_ids = {item["observation_id"] for item in source}
    for index, fact in enumerate(expected_difference_facts):
        fact_node = f"difference_fact:{index}"
        nodes.append(
            {
                "node_id": fact_node,
                "node_type": "DIFFERENCE_FACT",
                "fact": copy.deepcopy(fact),
            }
        )
        referenced_ids = {
            value
            for key, value in fact.items()
            if key.endswith("observation_id") and isinstance(value, str) and value in source_ids
        }
        if referenced_ids:
            for observation_id in sorted(referenced_ids):
                edges.append(
                    {
                        "from": f"observation:{observation_id}",
                        "to": fact_node,
                        "relationship": "SUPPORTS_DIFFERENCE_FACT",
                    }
                )
        else:
            edges.append(
                {"from": mutation_node, "to": fact_node, "relationship": "MATERIALIZES_FACT"}
            )
        edges.append({"from": fact_node, "to": break_node, "relationship": "CLASSIFIES_AS"})

    return {"nodes": nodes, "edges": edges}


def _coverage_manifest(
    config: GeneratorConfig,
    truth: list[dict[str, Any]],
    source: list[dict[str, Any]],
) -> dict[str, Any]:
    by_product: dict[str, dict[str, Any]] = {}
    for product in PRODUCTS:
        rows = [item for item in truth if item["product_type"] == product]
        by_family = {
            family: sum(item["break_family"] == family for item in rows)
            for family in BREAK_FAMILIES
        }
        by_portfolio = {
            portfolio: sum(item["portfolio_id"] == portfolio for item in rows)
            for portfolio in config.portfolio_ids
        }
        by_product[product] = {
            "clean": sum(item["population"] == "CLEAN" for item in rows),
            "mutated": sum(item["population"] == "MUTATED" for item in rows),
            "total": len(rows),
            "by_break_family": by_family,
            "by_portfolio": by_portfolio,
        }
    by_kind = {
        kind: sum(item["observation_kind"] == kind for item in source) for kind in _CONTRACT_BY_KIND
    }
    return {
        "manifest_version": "1.0.0",
        "generator_version": config.generator_version,
        "calendar_version": config.calendar_version,
        "tenant_id": config.tenant_id,
        "portfolio_ids": list(config.portfolio_ids),
        "scenario_count": len(truth),
        "clean_count": sum(item["population"] == "CLEAN" for item in truth),
        "mutated_count": sum(item["population"] == "MUTATED" for item in truth),
        "products": by_product,
        "break_families": {
            family: {
                product: sum(
                    item["break_family"] == family and item["product_type"] == product
                    for item in truth
                )
                for product in PRODUCTS
            }
            for family in BREAK_FAMILIES
        },
        "source_contracts": list(_CONTRACT_BY_KIND.values()),
        "source_observation_count": len(source),
        "source_observation_count_by_kind": by_kind,
        "truth_access_classification": "EVALUATOR_ONLY",
    }


def _evidence_manifest(
    config: GeneratorConfig,
    source: list[dict[str, Any]],
    truth: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_version": "1.0.0",
        "generator_version": config.generator_version,
        "seed": config.seed,
        "config_hash": _hash_json(config.as_dict()),
        "source_fixture_hash": _hash_json(source),
        "truth_ledger_hash": _hash_json(truth),
        "coverage_manifest_hash": _hash_json(coverage),
        "scenario_count": len(truth),
        "source_observation_count": len(source),
        "lineage_group_count": len({item["lineage_group_id"] for item in source}),
        "truth_access_classification": "EVALUATOR_ONLY",
    }


def _validate_sources(source: list[dict[str, Any]]) -> None:
    for observation in source:
        kind = cast(ObservationKind, observation["observation_kind"])
        validate_contract_document(_CONTRACT_BY_KIND[kind], observation)


def _find_observation(
    observations: list[dict[str, Any]], kind: ObservationKind
) -> dict[str, Any] | None:
    return next((item for item in observations if item["observation_kind"] == kind), None)


def _rename_payload_identity(
    observation: dict[str, Any], kind: ObservationKind, suffix: str
) -> None:
    payload = observation["payload"]
    if kind == "EXECUTION":
        payload["execution_id"] = f"exec_{suffix}"
        payload["order_id"] = f"order_{suffix}"
    elif kind == "TRADE_CAPTURE":
        payload["capture_id"] = f"capture_{suffix}"
        payload["execution_reference"] = f"exec_{suffix}"
    elif kind == "CONFIRMATION":
        payload["confirmation_id"] = f"confirmation_{suffix}"
        payload["confirmation_reference"] = f"CONF-{suffix.upper()}"
    elif kind == "BOOKING":
        payload["booking_record_id"] = f"booking_{suffix}"
        payload["confirmation_reference"] = f"CONF-{suffix.upper()}"
        payload["booking_version"] = 2
    else:
        raise AssertionError(f"unsupported identity rename kind: {kind}")


def _set_lifecycle(
    observation: dict[str, Any],
    kind: ObservationKind,
    lifecycle_status: str,
    operation: str,
) -> None:
    payload = observation["payload"]
    payload["lifecycle_status"] = lifecycle_status
    if kind == "EXECUTION":
        payload["execution_type"] = operation
        payload["execution_status"] = lifecycle_status
    elif kind == "TRADE_CAPTURE":
        payload["capture_type"] = operation
        payload["capture_status"] = "AMENDED" if operation == "AMEND" else "CANCELLED"
    elif kind == "CONFIRMATION":
        payload["confirmation_status"] = lifecycle_status
    elif kind == "BOOKING":
        payload["booking_status"] = lifecycle_status
    else:
        raise AssertionError(f"unsupported lifecycle kind: {kind}")


def _path_value(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.strip("/").split("/"):
        current = current[part]
    return copy.deepcopy(current)


def _recompute_hashes(observation: dict[str, Any]) -> None:
    payload = observation["payload"]
    if observation["observation_kind"] == "BOOKING":
        fingerprint_source = {
            key: value for key, value in payload.items() if key != "record_fingerprint"
        }
        payload["record_fingerprint"] = _hash_json(fingerprint_source)
    observation["content_hash"] = compute_observation_content_hash(observation)


def _decimal_string(value: Decimal, scale: int) -> str:
    return f"{value:.{scale}f}"


def _opaque_token(config: GeneratorConfig, namespace: str, *components: object) -> str:
    """Return a stable token with no runtime scenario/template semantics."""

    material = "\x1f".join(
        ("e3-opaque-v1", str(config.seed), namespace, *(str(component) for component in components))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _opaque_identifier(
    config: GeneratorConfig,
    prefix: str,
    namespace: str,
    *components: object,
) -> str:
    return f"{prefix}_{_opaque_token(config, namespace, *components)}"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hash_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
