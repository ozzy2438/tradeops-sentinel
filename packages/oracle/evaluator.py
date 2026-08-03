"""Independent oracle over the TS-11 source-version and watermark contract.

This module deliberately uses only the Python standard library.  It accepts
JSON-compatible mappings rather than Pydantic or reconciliation-engine
objects, and returns a compact expected-outcome projection.  The production
reconciliation result is never an input to the oracle.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import OracleContext

BREAK_FAMILIES: tuple[str, ...] = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
)

_REQUIRED_SOURCE_KINDS: tuple[str, ...] = ("EXECUTION", "CONFIRMATION", "BOOKING")
_COMPARABLE_FIELDS: tuple[str, ...] = (
    "/payload/base_currency",
    "/payload/terms_currency",
    "/payload/side",
)
_ECONOMIC_FIELDS: tuple[str, ...] = (
    "/payload/base_amount",
    "/payload/terms_amount",
    "/payload/quoted_rate",
)
_DATE_FIELDS: tuple[str, ...] = ("/payload/trade_date", "/payload/value_date")
_POST_ACTION_FIELDS: tuple[str, ...] = (
    "/payload/book_id",
    "/payload/lifecycle_status",
    "/payload/booking_version",
    "/payload/record_fingerprint",
)
_TERMINAL_STATUSES = frozenset({"AMENDED", "CANCELLED"})


@dataclass(frozen=True)
class OracleFact:
    """A compact, source-bound fact emitted by the independent oracle."""

    family: str
    field_path: str
    expected_value: str
    observed_value: str
    source_observation_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "field_path": self.field_path,
            "expected_value": self.expected_value,
            "observed_value": self.observed_value,
            "source_observation_ids": list(self.source_observation_ids),
        }


@dataclass(frozen=True)
class OracleResult:
    """Deterministic expected outcome, without production result objects."""

    oracle_version: str
    product_type: str
    source_watermark: str
    source_observation_ids: tuple[str, ...]
    config_hash: str
    families: tuple[str, ...]
    facts: tuple[OracleFact, ...]

    @property
    def result(self) -> str:
        return "BREAKS_DETECTED" if self.families else "PASS"

    def as_dict(self) -> dict[str, object]:
        return {
            "oracle_version": self.oracle_version,
            "product_type": self.product_type,
            "source_watermark": self.source_watermark,
            "source_observation_ids": list(self.source_observation_ids),
            "config_hash": self.config_hash,
            "result": self.result,
            "families": list(self.families),
            "facts": [fact.as_dict() for fact in self.facts],
        }


def evaluate(
    context: OracleContext | None = None,
    *,
    source_observations: Sequence[Mapping[str, Any]] | None = None,
    canonical_state: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    linkage_decision: Mapping[str, Any] | None = None,
    post_action_verification: Mapping[str, Any] | None = None,
    evaluated_at: str | None = None,
) -> OracleResult:
    """Evaluate source facts without importing production reconciliation code.

    ``canonical_state`` is a small JSON projection containing source watermark,
    source-version references, and field provenance.  It is intentionally not
    a ``CanonicalTradeState`` instance.  The caller may construct this
    projection from a validated contract, but the oracle only sees the
    resulting source facts.
    """

    if context is not None:
        return _evaluate_context(context)
    if source_observations is None or canonical_state is None or config is None:
        raise TypeError(
            "mapping evaluation requires source_observations, canonical_state, and config"
        )
    return _evaluate_mappings(
        source_observations=source_observations,
        canonical_state=canonical_state,
        config=config,
        linkage_decision=linkage_decision,
        post_action_verification=post_action_verification,
        evaluated_at=evaluated_at,
    )


def _evaluate_context(context: OracleContext) -> OracleResult:
    """Adapt the shared contract boundary to the oracle's JSON projection."""

    canonical = context.canonical_state
    observations = tuple(item.model_dump(mode="json") for item in context.source_observations)
    observations_by_id = {item.observation_id: item for item in context.source_observations}
    field_names = (
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
    canonical_projection: dict[str, Any] = {
        "tenant_id": canonical.tenant_id,
        "portfolio_id": canonical.portfolio_id,
        "correlation_id": canonical.correlation_id,
        "trade_id": canonical.trade_id,
        "product_type": canonical.state.product_type,
        "source_watermark": canonical.source_watermark.isoformat(),
        "field_provenance": {
            f"/payload/{field_name}": {
                "source_observation_id": getattr(
                    canonical.field_provenance, field_name
                ).source_observation_id
            }
            for field_name in field_names
        },
        "source_version_set": [
            {
                "source_observation_id": item.observation_id,
                "observation_kind": item.observation_kind,
                "source_system": item.source_system,
                "source_business_key": observations_by_id[item.observation_id].source_business_key,
                "source_version": item.source_version,
                "content_hash": item.content_hash,
            }
            for item in canonical.source_version_set
        ],
    }
    linkage = (
        None
        if context.linkage_decision is None
        else context.linkage_decision.model_dump(mode="json")
    )
    post_action = (
        None if context.post_action is None else context.post_action.model_dump(mode="json")
    )
    return _evaluate_mappings(
        source_observations=observations,
        canonical_state=canonical_projection,
        config=_fixture_oracle_config(),
        linkage_decision=linkage,
        post_action_verification=post_action,
        evaluated_at=context.effective_evaluated_at.isoformat(),
    )


def _fixture_oracle_config() -> dict[str, object]:
    """Return independently authored fixture policy inputs for parity tests."""

    products = ("FX_SPOT", "FX_FORWARD")
    source_systems = {
        "EXECUTION": "FIX_EXECUTION",
        "CONFIRMATION": "FPML_CONFIRMATION",
        "BOOKING": "MOCK_LEGACY_BOOKING",
    }
    arrival_windows = [
        {
            "product_type": product,
            "observation_kind": kind,
            "source_system": source_systems[kind],
            "window_seconds": 0,
        }
        for product in products
        for kind in ("EXECUTION", "CONFIRMATION", "BOOKING")
    ]
    decimal_tolerances = [
        {
            "product_type": product,
            "field_path": field_path,
            "tolerance": {
                "mode": "ABSOLUTE_DECIMAL",
                "value": "0.0001" if field_path.endswith("quoted_rate") else "0.01",
            },
        }
        for product in products
        for field_path in _ECONOMIC_FIELDS
    ]
    return {
        "config_id": "ts12_fixture_oracle_policy",
        "config_version": "1.0.0",
        "arrival_windows": arrival_windows,
        "decimal_tolerances": decimal_tolerances,
        "lifecycle_expected_statuses": [
            {"observation_kind": "EXECUTION", "expected_status": "NEW"},
            {"observation_kind": "TRADE_CAPTURE", "expected_status": "CAPTURED"},
            {"observation_kind": "CONFIRMATION", "expected_status": "CONFIRMED"},
            {"observation_kind": "BOOKING", "expected_status": "BOOKED"},
        ],
    }


def _evaluate_mappings(
    *,
    source_observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    config: Mapping[str, Any],
    linkage_decision: Mapping[str, Any] | None,
    post_action_verification: Mapping[str, Any] | None,
    evaluated_at: str | None,
) -> OracleResult:
    observations = tuple(source_observations)
    _validate_input(observations, canonical_state, config, linkage_decision)
    product_type = _string(canonical_state, "product_type")
    watermark = _string(canonical_state, "source_watermark")
    evaluation_time = evaluated_at or watermark
    config_hash = _stable_hash(config)
    facts: list[OracleFact] = []

    facts.extend(_missing_source_facts(observations, canonical_state, config, evaluation_time))
    facts.extend(_linkage_facts(observations, canonical_state, linkage_decision))
    facts.extend(_duplicate_facts(observations))
    facts.extend(_comparison_facts(observations, canonical_state, "/payload/base_currency"))
    facts.extend(_comparison_facts(observations, canonical_state, "/payload/terms_currency"))
    facts.extend(_comparison_facts(observations, canonical_state, "/payload/side"))
    facts.extend(_economic_facts(observations, canonical_state, config))
    facts.extend(_date_facts(observations, canonical_state))
    facts.extend(_lifecycle_facts(observations, config))
    facts.extend(_post_action_facts(post_action_verification))

    families = tuple(
        family for family in BREAK_FAMILIES if any(fact.family == family for fact in facts)
    )
    facts.sort(
        key=lambda fact: (
            BREAK_FAMILIES.index(fact.family),
            fact.field_path,
            fact.source_observation_ids,
        )
    )
    return OracleResult(
        oracle_version="1.0.0",
        product_type=product_type,
        source_watermark=watermark,
        source_observation_ids=tuple(
            _string(observation, "observation_id")
            for observation in _ordered_observations(observations)
        ),
        config_hash=config_hash,
        families=families,
        facts=tuple(facts),
    )


def _validate_input(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    config: Mapping[str, Any],
    linkage_decision: Mapping[str, Any] | None,
) -> None:
    if not observations:
        raise ValueError("oracle requires at least one source observation")
    forbidden_keys = {"reconciliation_run", "breaks", "break_ids", "result"}
    if forbidden_keys.intersection(canonical_state):
        raise ValueError("oracle input must not contain production evaluator output")
    if not _string(canonical_state, "source_watermark"):
        raise ValueError("canonical source_watermark is required")
    source_ids = [_string(observation, "observation_id") for observation in observations]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("oracle source observation IDs must be unique")
    scope = tuple(
        _string(canonical_state, field) for field in ("tenant_id", "portfolio_id", "correlation_id")
    )
    for observation in observations:
        observation_scope = tuple(
            _string(observation, field) for field in ("tenant_id", "portfolio_id", "correlation_id")
        )
        if observation_scope != scope:
            raise ValueError("oracle source observations must remain in canonical scope")
        if _parse_time(_string(observation, "ingest_time")) > _parse_time(
            _string(canonical_state, "source_watermark")
        ):
            raise ValueError("oracle source observation arrives after canonical watermark")
    expected_refs = canonical_state.get("source_version_set")
    if not isinstance(expected_refs, Sequence) or isinstance(expected_refs, (str, bytes)):
        raise ValueError("canonical source_version_set is required")
    actual_keys = {
        (
            _string(observation, "observation_id"),
            _string(observation, "observation_kind"),
            _string(observation, "source_system"),
            _string(observation, "source_version"),
            _string(observation, "content_hash"),
        )
        for observation in observations
    }
    expected_keys = {
        (
            _string(reference, "source_observation_id"),
            _string(reference, "observation_kind"),
            _string(reference, "source_system"),
            _string(reference, "source_version"),
            _string(reference, "content_hash"),
        )
        for reference in expected_refs
        if isinstance(reference, Mapping)
    }
    if actual_keys != expected_keys:
        raise ValueError("oracle source observations must equal canonical source_version_set")
    if linkage_decision is not None:
        source_id = linkage_decision.get("source_observation_id")
        if source_id is not None and str(source_id) not in source_ids:
            raise ValueError("oracle linkage decision must reference the source set")
    if not config.get("config_id") or not config.get("config_version"):
        raise ValueError("oracle config identity is required")


def _missing_source_facts(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    config: Mapping[str, Any],
    evaluated_at: str,
) -> list[OracleFact]:
    available = {_string(observation, "observation_kind") for observation in observations}
    product = _string(canonical_state, "product_type")
    watermark = _parse_time(_string(canonical_state, "source_watermark"))
    evaluated = _parse_time(evaluated_at)
    rules = config.get("arrival_windows", ())
    facts: list[OracleFact] = []
    for kind in _REQUIRED_SOURCE_KINDS:
        if kind in available:
            continue
        window = _window_seconds(rules, product, kind)
        if evaluated < watermark + timedelta(seconds=window):
            continue
        observed_id = _string(_ordered_observations(observations)[0], "observation_id")
        facts.append(
            OracleFact(
                family="MISSING_REQUIRED_SOURCE",
                field_path=f"/source/{kind.lower()}_observation",
                expected_value="present",
                observed_value="absent_after_watermark",
                source_observation_ids=(observed_id,),
            )
        )
    return facts


def _linkage_facts(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
) -> list[OracleFact]:
    expected_trade_id = _string(canonical_state, "trade_id")
    candidates: Sequence[object] = ()
    invalid = decision is None
    if decision is not None:
        raw_candidates = decision.get("candidate_links", ())
        if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes)):
            candidates = raw_candidates
        candidate_scope_invalid = any(
            not isinstance(candidate, Mapping)
            or _string(candidate, "tenant_id") != _string(canonical_state, "tenant_id")
            or _string(candidate, "portfolio_id") != _string(canonical_state, "portfolio_id")
            for candidate in candidates
        )
        invalid = (
            candidate_scope_invalid
            or _string(decision, "decision")
            in {"REJECTED", "UNMATCHED", "AMBIGUOUS", "CROSS_SCOPE_REJECTED"}
            or decision.get("chosen_trade_id") != expected_trade_id
            or len(candidates) != 1
        )
    if not invalid:
        return []
    count = str(len(candidates))
    if count == "1":
        count = "0"
    observed_id = _string(_ordered_observations(observations)[0], "observation_id")
    return [
        OracleFact(
            family="AMBIGUOUS_OR_UNMATCHED_LINKAGE",
            field_path="/linkage/trade_id",
            expected_value="1",
            observed_value=count,
            source_observation_ids=(observed_id,),
        )
    ]


def _duplicate_facts(observations: Sequence[Mapping[str, Any]]) -> list[OracleFact]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[
            (
                _string(observation, "observation_kind"),
                _string(observation, "source_business_key"),
                _string(observation, "source_version"),
            )
        ].append(observation)
    facts: list[OracleFact] = []
    for group in groups.values():
        if len({_string(item, "content_hash") for item in group}) < 2:
            continue
        ordered = _ordered_observations(group)
        facts.append(
            OracleFact(
                family="DUPLICATE_SOURCE_CONFLICT",
                field_path="/source/content_hash",
                expected_value=_string(ordered[0], "content_hash"),
                observed_value=_string(ordered[1], "content_hash"),
                source_observation_ids=tuple(_string(item, "observation_id") for item in ordered),
            )
        )
    return facts


def _comparison_facts(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    field_path: str,
) -> list[OracleFact]:
    baseline = _authoritative(observations, canonical_state, field_path)
    expected = _field_value(baseline, field_path)
    for observed in _ordered_observations(observations):
        if _string(observed, "observation_id") == _string(baseline, "observation_id"):
            continue
        observed_value = _field_value(observed, field_path)
        if observed_value == expected:
            continue
        return [
            OracleFact(
                family="CURRENCY_PAIR_OR_SIDE_MISMATCH",
                field_path=field_path,
                expected_value=expected,
                observed_value=observed_value,
                source_observation_ids=(
                    _string(baseline, "observation_id"),
                    _string(observed, "observation_id"),
                ),
            )
        ]
    return []


def _economic_facts(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[OracleFact]:
    rules = config.get("decimal_tolerances", ())
    facts: list[OracleFact] = []
    for field_path in _ECONOMIC_FIELDS:
        baseline = _authoritative(observations, canonical_state, field_path)
        expected = _decimal(_field_value(baseline, field_path))
        tolerance = _decimal_rule(rules, _string(canonical_state, "product_type"), field_path)
        for observed in _ordered_observations(observations):
            if _string(observed, "observation_id") == _string(baseline, "observation_id"):
                continue
            observed_value = _field_value(observed, field_path)
            if _within_tolerance(expected, _decimal(observed_value), tolerance):
                continue
            facts.append(
                OracleFact(
                    family="ECONOMIC_VALUE_MISMATCH",
                    field_path=field_path,
                    expected_value=str(expected),
                    observed_value=observed_value,
                    source_observation_ids=(
                        _string(baseline, "observation_id"),
                        _string(observed, "observation_id"),
                    ),
                )
            )
            break
    return facts


def _date_facts(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
) -> list[OracleFact]:
    facts: list[OracleFact] = []
    for field_path in _DATE_FIELDS:
        baseline = _authoritative(observations, canonical_state, field_path)
        expected = _field_value(baseline, field_path)
        for observed in _ordered_observations(observations):
            if _string(observed, "observation_id") == _string(baseline, "observation_id"):
                continue
            observed_value = _field_value(observed, field_path)
            if observed_value == expected:
                continue
            facts.append(
                OracleFact(
                    family="TRADE_OR_VALUE_DATE_MISMATCH",
                    field_path=field_path,
                    expected_value=expected,
                    observed_value=observed_value,
                    source_observation_ids=(
                        _string(baseline, "observation_id"),
                        _string(observed, "observation_id"),
                    ),
                )
            )
            break
    return facts


def _lifecycle_facts(
    observations: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[OracleFact]:
    statuses = [
        _field_value(observation, "/payload/lifecycle_status") for observation in observations
    ]
    if len(set(statuses)) == 1 and statuses[0] in _TERMINAL_STATUSES:
        return []
    expected_statuses = {
        _string(rule, "observation_kind"): _string(rule, "expected_status")
        for rule in _sequence(config.get("lifecycle_expected_statuses", ()))
        if isinstance(rule, Mapping)
    }
    for observation in _ordered_observations(observations):
        kind = _string(observation, "observation_kind")
        observed = _field_value(observation, "/payload/lifecycle_status")
        expected = expected_statuses.get(kind)
        if expected is None or observed != expected:
            return [
                OracleFact(
                    family="LIFECYCLE_STATUS_MISMATCH",
                    field_path="/payload/lifecycle_status",
                    expected_value=expected or "configured_expected_status",
                    observed_value=observed,
                    source_observation_ids=(_string(observation, "observation_id"),),
                )
            ]
    return []


def _post_action_facts(
    verification: Mapping[str, Any] | None,
) -> list[OracleFact]:
    if verification is None:
        return []
    pre_action = verification.get("pre_action")
    post_action = verification.get("post_action")
    if not isinstance(pre_action, Mapping):
        raise ValueError("post-action verification requires pre_action")
    explicit = verification.get("changed_fields", ())
    changed: list[tuple[str, str, str, tuple[str, ...]]] = []
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        for item in explicit:
            if not isinstance(item, Mapping):
                raise ValueError("changed_fields must contain mappings")
            path = _string(item, "field_path")
            changed.append(
                (
                    path,
                    _string(item, "expected_value"),
                    _string(item, "observed_value"),
                    (_string(pre_action, "observation_id"),),
                )
            )
    if isinstance(post_action, Mapping):
        for field_path in _POST_ACTION_FIELDS:
            expected = _field_value(pre_action, field_path)
            observed = _field_value(post_action, field_path)
            if expected != observed and not any(item[0] == field_path for item in changed):
                changed.append(
                    (
                        field_path,
                        expected,
                        observed,
                        (
                            _string(pre_action, "observation_id"),
                            _string(post_action, "observation_id"),
                        ),
                    )
                )
    readback_available = bool(verification.get("readback_available", True))
    original_break_remaining = bool(verification.get("original_break_remaining", False))
    if not readback_available and not changed:
        changed.append(
            (
                "/payload/book_id",
                _field_value(pre_action, "/payload/book_id"),
                "readback_unavailable",
                (_string(pre_action, "observation_id"),),
            )
        )
    if not changed and not original_break_remaining and readback_available:
        return []
    if not changed:
        changed.append(
            (
                "/payload/book_id",
                _field_value(pre_action, "/payload/book_id"),
                "readback_unavailable",
                (_string(pre_action, "observation_id"),),
            )
        )
    facts: list[OracleFact] = []
    for field_path, expected, observed, source_ids in changed:
        facts.append(
            OracleFact(
                family="POST_ACTION_VERIFICATION_FAILURE",
                field_path=field_path,
                expected_value=expected,
                observed_value=observed,
                source_observation_ids=source_ids,
            )
        )
    return facts


def _authoritative(
    observations: Sequence[Mapping[str, Any]],
    canonical_state: Mapping[str, Any],
    field_path: str,
) -> Mapping[str, Any]:
    provenance = canonical_state.get("field_provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("canonical field_provenance is required")
    reference = provenance.get(field_path)
    if isinstance(reference, Mapping):
        source_id = reference.get("source_observation_id")
    else:
        source_id = reference
    if not isinstance(source_id, str):
        raise ValueError(f"canonical provenance is missing for {field_path}")
    for observation in observations:
        if _string(observation, "observation_id") == source_id:
            return observation
    raise ValueError(f"canonical provenance references unavailable observation: {source_id}")


def _field_value(observation: Mapping[str, Any], field_path: str) -> str:
    leaf = field_path.rsplit("/", 1)[1]
    payload = observation.get("payload")
    if not isinstance(payload, Mapping) or leaf not in payload:
        raise ValueError(f"observation does not contain {field_path}")
    value = payload[leaf]
    if isinstance(value, Mapping) and "value" in value:
        return str(value["value"])
    return str(value)


def _ordered_observations(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    rank = {"EXECUTION": 1, "TRADE_CAPTURE": 2, "CONFIRMATION": 3, "BOOKING": 4}
    return tuple(
        sorted(
            observations,
            key=lambda observation: (
                rank.get(_string(observation, "observation_kind"), 99),
                int(observation.get("source_sequence", 0)),
                _string(observation, "observation_id"),
                _string(observation, "source_version"),
                _string(observation, "content_hash"),
            ),
        )
    )


def _window_seconds(rules: object, product: str, kind: str) -> int:
    for rule in _sequence(rules):
        if (
            isinstance(rule, Mapping)
            and _string(rule, "product_type") == product
            and _string(rule, "observation_kind") == kind
        ):
            return int(rule.get("window_seconds", 0))
    raise ValueError(f"missing arrival-window rule for {product}/{kind}")


def _decimal_rule(rules: object, product: str, field_path: str) -> tuple[str, Decimal]:
    for rule in _sequence(rules):
        if (
            isinstance(rule, Mapping)
            and _string(rule, "product_type") == product
            and _string(rule, "field_path") == field_path
        ):
            tolerance = rule.get("tolerance")
            if not isinstance(tolerance, Mapping):
                raise ValueError("decimal rule tolerance is required")
            mode = _string(tolerance, "mode")
            value = tolerance.get("value")
            return mode, Decimal(str(value)) if value is not None else Decimal("0")
    raise ValueError(f"missing decimal rule for {product}/{field_path}")


def _within_tolerance(expected: Decimal, observed: Decimal, rule: tuple[str, Decimal]) -> bool:
    mode, allowed = rule
    difference = abs(observed - expected)
    if mode == "NONE":
        return difference == 0
    if mode == "ABSOLUTE_DECIMAL":
        return difference <= allowed
    if mode == "RELATIVE_DECIMAL":
        return difference <= abs(expected) * allowed
    raise ValueError(f"unsupported decimal tolerance mode: {mode}")


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value}") from exc


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("oracle timestamps must include a timezone offset")
    return parsed


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return value


def _string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if value is None:
        raise ValueError(f"missing required oracle field: {key}")
    return str(value)


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
