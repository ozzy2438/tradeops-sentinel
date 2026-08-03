"""Policy-enforced construction of versioned canonical trade state.

The full locked source set is kept separate from the per-field selection.
The resolver derives that selection from the packaged, versioned
``SourceOfTruthPolicy``; the assembler recomputes it and fails closed if a
caller attempts to substitute an unauthorised source.  Every observed source
still remains in ``source_version_set`` so reconciliation can evaluate
conflicts, missing sources, and non-authoritative values without weakening
canonical provenance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from importlib.resources import files
from typing import Any, Literal

from packages.contracts.hashing import validate_observation_content_hash
from packages.contracts.models import (
    Actor,
    CanonicalFields,
    CanonicalTradeState,
    FieldProvenance,
    FieldProvenanceMap,
    ObservationEnvelope,
    SourceOfTruthPolicy,
    SourceVersionSetItem,
)

_NORMALISATION_RULE_ID = "ts10-explicit-field-selection"
_NORMALISATION_RULE_VERSION = "1.0.0"

CANONICAL_FIELD_NAMES: tuple[str, ...] = (
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

FieldSelection = Mapping[str, ObservationEnvelope]
"""Maps each canonical field to a policy-resolved source observation."""

_SUPPORTED_SOURCE_OF_TRUTH_POLICY_VERSION = "1.0.0"


class CanonicalAssemblyError(ValueError):
    """Base class for fail-closed canonical assembly errors."""


class SourceObservationSetError(CanonicalAssemblyError):
    """Raised when the locked full source set is incomplete or cross-scope."""


class SourceOfTruthPolicyVersionError(CanonicalAssemblyError):
    """Raised when the assembler does not implement the supplied policy version."""


class SourceOfTruthPolicyContentError(CanonicalAssemblyError):
    """Raised when policy content differs from the packaged approved version."""

    def __init__(self, *, expected_hash: str, received_hash: str) -> None:
        self.expected_hash = expected_hash
        self.received_hash = received_hash
        super().__init__(
            "source-of-truth policy content is not the packaged approved policy: "
            f"expected_hash={expected_hash!r} received_hash={received_hash!r}"
        )


class SourceOfTruthSelectionError(CanonicalAssemblyError):
    """Raised when caller selection differs from deterministic policy resolution."""

    def __init__(
        self,
        *,
        field_name: str,
        selected: ObservationEnvelope,
        required: ObservationEnvelope,
        policy_version: str,
    ) -> None:
        self.field_name = field_name
        self.selected_observation_id = selected.observation_id
        self.required_observation_id = required.observation_id
        self.policy_version = policy_version
        super().__init__(
            "source-of-truth selection is not authorised: "
            f"field={field_name!r} selected_observation_id={selected.observation_id!r} "
            f"selected_source_system={selected.source_system!r} "
            f"required_observation_id={required.observation_id!r} "
            f"required_source_system={required.source_system!r} "
            f"policy_version={policy_version!r}"
        )


def load_mvp_source_of_truth_policy() -> SourceOfTruthPolicy:
    """Load the packaged, versioned ADR-001 MVP policy artefact."""

    policy_path = files("packages.contracts").joinpath(
        "examples", "valid", "source-of-truth-policy.json"
    )
    return SourceOfTruthPolicy.model_validate_json(policy_path.read_text(encoding="utf-8"))


def _policy_content_hash(policy: SourceOfTruthPolicy) -> str:
    canonical = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_policy(policy: SourceOfTruthPolicy) -> None:
    if policy.policy_version != _SUPPORTED_SOURCE_OF_TRUTH_POLICY_VERSION:
        raise SourceOfTruthPolicyVersionError(
            "unsupported source-of-truth policy version: "
            f"expected={_SUPPORTED_SOURCE_OF_TRUTH_POLICY_VERSION!r} "
            f"received={policy.policy_version!r}"
        )
    expected_hash = _policy_content_hash(load_mvp_source_of_truth_policy())
    received_hash = _policy_content_hash(policy)
    if received_hash != expected_hash:
        raise SourceOfTruthPolicyContentError(
            expected_hash=expected_hash,
            received_hash=received_hash,
        )


def _source_reference(observation: ObservationEnvelope) -> tuple[str, str, str, str, str]:
    return (
        observation.observation_id,
        observation.observation_kind,
        observation.source_system,
        observation.source_version,
        observation.content_hash,
    )


def _locked_source_set(
    source_observations: Iterable[ObservationEnvelope],
) -> tuple[ObservationEnvelope, ...]:
    observations = tuple(source_observations)
    if not observations:
        raise SourceObservationSetError("source_observations must not be empty")
    for observation in observations:
        validate_observation_content_hash(observation)

    observation_ids = [observation.observation_id for observation in observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise SourceObservationSetError("source_observations observation IDs must be unique")

    anchor_scope = (
        observations[0].tenant_id,
        observations[0].portfolio_id,
        observations[0].correlation_id,
    )
    for observation in observations[1:]:
        if (
            observation.tenant_id,
            observation.portfolio_id,
            observation.correlation_id,
        ) != anchor_scope:
            raise SourceObservationSetError(
                "source_observations must remain inside one tenant/portfolio/correlation scope"
            )
    return tuple(
        sorted(
            observations,
            key=lambda item: (
                item.observation_kind,
                item.source_system,
                item.source_business_key,
                int(item.source_version),
                item.observation_id,
                item.content_hash,
            ),
        )
    )


def _eligible_for_trade(
    observation: ObservationEnvelope,
    trade_id: str,
) -> bool:
    return (
        observation.source_business_key == trade_id
        or observation.payload.source_trade_id == trade_id
    )


def _resolved_observation(
    *,
    field_name: str,
    trade_id: str,
    source_observations: Sequence[ObservationEnvelope],
    policy: SourceOfTruthPolicy,
) -> ObservationEnvelope:
    field_path = f"/payload/{field_name}"
    rule = next((item for item in policy.field_rules if item.field_path == field_path), None)
    if rule is None:
        raise SourceObservationSetError(f"source-of-truth policy has no rule for {field_path}")
    eligible = [
        observation
        for observation in source_observations
        if _eligible_for_trade(observation, trade_id)
    ]
    for source_system in rule.source_precedence:
        candidates = [item for item in eligible if item.source_system == source_system]
        if not candidates:
            continue
        # Higher source versions supersede lower versions.  A same-version
        # conflict remains visible in the full source set and is resolved to a
        # stable operand solely so reconciliation can emit the typed conflict.
        return sorted(
            candidates,
            key=lambda item: (-int(item.source_version), item.observation_id, item.content_hash),
        )[0]
    raise SourceObservationSetError(
        f"no policy-authorised source is available for canonical field {field_path}"
    )


def resolve_field_selection(
    *,
    trade_id: str,
    source_observations: Iterable[ObservationEnvelope],
    source_of_truth_policy: SourceOfTruthPolicy,
) -> dict[str, ObservationEnvelope]:
    """Resolve every canonical field deterministically under a versioned policy."""

    _validate_policy(source_of_truth_policy)
    locked_sources = _locked_source_set(source_observations)
    return {
        field_name: _resolved_observation(
            field_name=field_name,
            trade_id=trade_id,
            source_observations=locked_sources,
            policy=source_of_truth_policy,
        )
        for field_name in CANONICAL_FIELD_NAMES
    }


def _field_value(observation: ObservationEnvelope, field_name: str) -> Any:
    return getattr(observation.payload, field_name)


def _canonical_fields_from_selection(field_selection: FieldSelection) -> CanonicalFields:
    values = {
        field_name: _field_value(field_selection[field_name], field_name)
        for field_name in CANONICAL_FIELD_NAMES
    }
    return CanonicalFields(**values)


def _field_provenance_map(
    field_selection: FieldSelection,
    source_of_truth_policy: SourceOfTruthPolicy,
) -> FieldProvenanceMap:
    fields: dict[str, FieldProvenance] = {}
    for field_name in CANONICAL_FIELD_NAMES:
        observation = field_selection[field_name]
        field_path = f"/payload/{field_name}"
        rule = next(
            item for item in source_of_truth_policy.field_rules if item.field_path == field_path
        )
        conflict_status: Literal["SELECTED", "SECONDARY_SUPPORTING"] = (
            "SELECTED"
            if observation.source_system in rule.trusted_sources
            else "SECONDARY_SUPPORTING"
        )
        fields[field_name] = FieldProvenance(
            source_type=observation.observation_kind,
            source_system=observation.source_system,
            source_tenant_id=observation.tenant_id,
            source_portfolio_id=observation.portfolio_id,
            source_observation_id=observation.observation_id,
            source_observation_entity_version=observation.entity_version,
            source_version=observation.source_version,
            field_path=field_path,
            normalisation_rule_id=_NORMALISATION_RULE_ID,
            normalisation_rule_version=_NORMALISATION_RULE_VERSION,
            resolution_rule_version=source_of_truth_policy.policy_version,
            observed_at=observation.event_time,
            effective_at=observation.effective_time,
            ingested_at=observation.ingest_time,
            conflict_status=conflict_status,
        )
    return FieldProvenanceMap(**fields)


def _validate_consistent_observation_identity(field_selection: FieldSelection) -> None:
    """Reject a field_selection where two different envelope objects share
    an observation_id but disagree on content_hash (Fizz, 2026-08-02T23:58,
    finding 1). Without this check, deduplication in
    ``_source_version_set`` would silently pick an arbitrary one of the
    two while ``_field_provenance_map``/``_canonical_fields_from_selection``
    could use the other's field values — an ambiguous, inconsistent
    provenance the caller would have no way to detect."""

    content_hash_by_observation_id: dict[str, str] = {}
    for observation in field_selection.values():
        validate_observation_content_hash(observation)
        seen_hash = content_hash_by_observation_id.get(observation.observation_id)
        if seen_hash is None:
            content_hash_by_observation_id[observation.observation_id] = observation.content_hash
        elif seen_hash != observation.content_hash:
            raise ValueError(
                "field_selection contains inconsistent envelopes for "
                f"observation_id={observation.observation_id!r}: content_hash "
                f"{seen_hash!r} and {observation.content_hash!r} disagree"
            )


def _source_version_set(
    source_observations: Sequence[ObservationEnvelope],
) -> list[SourceVersionSetItem]:
    return [
        SourceVersionSetItem(
            observation_id=observation.observation_id,
            observation_kind=observation.observation_kind,
            source_system=observation.source_system,
            source_tenant_id=observation.tenant_id,
            source_portfolio_id=observation.portfolio_id,
            source_version=observation.source_version,
            content_hash=observation.content_hash,
        )
        for observation in source_observations
    ]


def _content_hash(state: CanonicalFields, source_version_set: list[SourceVersionSetItem]) -> str:
    payload: dict[str, Any] = {
        "state": json.loads(state.model_dump_json()),
        "source_version_set": [json.loads(item.model_dump_json()) for item in source_version_set],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assemble_canonical_state(
    *,
    trade_id: str,
    canonical_state_version: int,
    field_selection: FieldSelection,
    source_observations: Iterable[ObservationEnvelope],
    source_of_truth_policy: SourceOfTruthPolicy,
    correlation_id: str,
    actor: Actor,
) -> CanonicalTradeState:
    """Build the next append-only projection from a policy-checked selection.

    Raises ``KeyError`` if ``field_selection`` does not have *exactly* the
    canonical field names as keys — missing fields or unrecognised extra
    keys are both rejected fail-closed (Honey, 2026-08-02T23:46, finding
    3). Raises ``ValueError`` if two different envelopes in the selection
    share an ``observation_id`` but disagree on ``content_hash`` — an
    ambiguous duplicate identity that would otherwise leave the
    ``state``/``field_provenance``/``source_version_set`` inconsistent
    with each other (Fizz, 2026-08-02T23:58, finding 1). Also lets the
    underlying Pydantic validators reject a selection that spans more
    than one tenant/portfolio scope (``CanonicalTradeState`` enforces
    this — see ``_validate_source_version_set_scope`` in
    ``packages/contracts/models.py``).

    The caller is responsible for choosing ``canonical_state_version``
    (typically one greater than the highest existing version for
    ``trade_id``) and for INSERTing the result rather than updating any
    prior row — this function has no persistence side effects itself.
    """

    selected_fields = set(field_selection)
    missing = set(CANONICAL_FIELD_NAMES) - selected_fields
    if missing:
        raise KeyError(f"field_selection is missing required canonical fields: {sorted(missing)}")
    extra = selected_fields - set(CANONICAL_FIELD_NAMES)
    if extra:
        raise KeyError(f"field_selection has unrecognised canonical fields: {sorted(extra)}")
    _validate_policy(source_of_truth_policy)
    locked_sources = _locked_source_set(source_observations)
    _validate_consistent_observation_identity(field_selection)
    expected_selection = resolve_field_selection(
        trade_id=trade_id,
        source_observations=locked_sources,
        source_of_truth_policy=source_of_truth_policy,
    )
    for field_name in CANONICAL_FIELD_NAMES:
        selected = field_selection[field_name]
        required = expected_selection[field_name]
        if _source_reference(selected) != _source_reference(required):
            raise SourceOfTruthSelectionError(
                field_name=field_name,
                selected=selected,
                required=required,
                policy_version=source_of_truth_policy.policy_version,
            )

    # Build from the observations resolved inside this trust boundary, not
    # caller-owned objects that merely carry matching references.
    state = _canonical_fields_from_selection(expected_selection)
    field_provenance = _field_provenance_map(expected_selection, source_of_truth_policy)
    source_version_set = _source_version_set(locked_sources)

    tenant_id = locked_sources[0].tenant_id
    portfolio_id = locked_sources[0].portfolio_id
    if correlation_id != locked_sources[0].correlation_id:
        raise SourceObservationSetError(
            "canonical correlation_id must match the locked source observation set"
        )
    source_watermark = max(observation.ingest_time for observation in locked_sources)

    return CanonicalTradeState(
        trade_id=trade_id,
        entity_version=1,
        canonical_state_version=canonical_state_version,
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        correlation_id=correlation_id,
        content_hash=_content_hash(state, source_version_set),
        as_of_time=source_watermark,
        source_watermark=source_watermark,
        source_version_set=source_version_set,
        actor=actor,
        state=state,
        field_provenance=field_provenance,
    )
