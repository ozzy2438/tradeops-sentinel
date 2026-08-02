"""Canonical assembler: builds a versioned CanonicalTradeState from an
already-resolved per-field selection (issue #10).

Scope boundary (Honey, 2026-08-02T23:38): the assembler must NOT infer
per-field precedence by promoting every canonical field from a single
arbitrary observation. ADR-001 assigns field-level authority explicitly —
execution/trade-capture for economics, confirmation for its own
status/content, booking for current booking values — and comparing or
ranking sources against that authority table is reconciliation (TS-11),
out of scope here. This module instead takes a ``field_selection``: an
explicit mapping of each canonical field name to the single observation
already chosen as authoritative for it (by the caller — a test fixture
today, a future TS-11-adjacent resolver later). The assembler's own job is
purely persistence-shaped: read the selected value, build correct
field-level provenance and a source watermark, and produce the next
append-only version — it never mutates or discards a prior version, and it
never makes a selection decision itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from packages.contracts.models import (
    Actor,
    CanonicalFields,
    CanonicalTradeState,
    FieldProvenance,
    FieldProvenanceMap,
    ObservationEnvelope,
    SourceVersionSetItem,
)

_NORMALISATION_RULE_ID = "ts10-explicit-field-selection"
_NORMALISATION_RULE_VERSION = "1.0.0"
_RESOLUTION_RULE_VERSION = "1.0.0"

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
"""Maps each canonical field name to the observation already chosen as
authoritative for it. Must contain exactly ``CANONICAL_FIELD_NAMES``; the
same observation may be selected for multiple fields (e.g. all of an
execution's economics), but the mapping itself — not this module — is
where that choice is made."""


def _field_value(observation: ObservationEnvelope, field_name: str) -> Any:
    return getattr(observation.payload, field_name)


def _canonical_fields_from_selection(field_selection: FieldSelection) -> CanonicalFields:
    values = {
        field_name: _field_value(field_selection[field_name], field_name)
        for field_name in CANONICAL_FIELD_NAMES
    }
    return CanonicalFields(**values)


def _field_provenance_map(field_selection: FieldSelection) -> FieldProvenanceMap:
    fields: dict[str, FieldProvenance] = {}
    for field_name in CANONICAL_FIELD_NAMES:
        observation = field_selection[field_name]
        fields[field_name] = FieldProvenance(
            source_type=observation.observation_kind,
            source_system=observation.source_system,
            source_tenant_id=observation.tenant_id,
            source_portfolio_id=observation.portfolio_id,
            source_observation_id=observation.observation_id,
            source_observation_entity_version=observation.entity_version,
            source_version=observation.source_version,
            field_path=f"/payload/{field_name}",
            normalisation_rule_id=_NORMALISATION_RULE_ID,
            normalisation_rule_version=_NORMALISATION_RULE_VERSION,
            resolution_rule_version=_RESOLUTION_RULE_VERSION,
            observed_at=observation.event_time,
            effective_at=observation.effective_time,
            ingested_at=observation.ingest_time,
            conflict_status="SELECTED",
        )
    return FieldProvenanceMap(**fields)


def _source_version_set(field_selection: FieldSelection) -> list[SourceVersionSetItem]:
    # Deduplicate by observation_id: several fields may point at the same
    # contributing observation (e.g. one execution supplying all of its
    # own economics).
    by_observation_id: dict[str, ObservationEnvelope] = {
        observation.observation_id: observation for observation in field_selection.values()
    }
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
        for observation in sorted(by_observation_id.values(), key=lambda item: item.observation_id)
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
    correlation_id: str,
    actor: Actor,
) -> CanonicalTradeState:
    """Build the next append-only canonical projection version from an
    already-resolved ``field_selection``.

    Raises ``KeyError`` if ``field_selection`` is missing any canonical
    field, and lets the underlying Pydantic validators reject a selection
    that spans more than one tenant/portfolio scope (``CanonicalTradeState``
    enforces this — see ``_validate_source_version_set_scope`` in
    ``packages/contracts/models.py``).

    The caller is responsible for choosing ``canonical_state_version``
    (typically one greater than the highest existing version for
    ``trade_id``) and for INSERTing the result rather than updating any
    prior row — this function has no persistence side effects itself.
    """

    missing = set(CANONICAL_FIELD_NAMES) - set(field_selection)
    if missing:
        raise KeyError(f"field_selection is missing required canonical fields: {sorted(missing)}")

    state = _canonical_fields_from_selection(field_selection)
    field_provenance = _field_provenance_map(field_selection)
    source_version_set = _source_version_set(field_selection)

    tenant_id = field_selection[CANONICAL_FIELD_NAMES[0]].tenant_id
    portfolio_id = field_selection[CANONICAL_FIELD_NAMES[0]].portfolio_id
    source_watermark = max(observation.ingest_time for observation in field_selection.values())

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
