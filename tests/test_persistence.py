"""TS-10 tests: source_event_inbox ingest decisions + canonical assembler.

Required tests per issue #10: replay, late-arrival, duplicate-vs-conflict.
Also covers Honey's identity-vs-delivery clarification (2026-08-02T23:34)
and the append-only "no destructive overwrite" acceptance criterion.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts.models import (
    Actor,
    ConfirmationObservation,
    ConfirmationPayload,
    DecimalAmount,
    DecimalRate,
    ExecutionObservation,
    ExecutionPayload,
)
from packages.persistence import (
    InboxStore,
    IngestOutcome,
    SourceConflictError,
    assemble_canonical_state,
    identity_key,
)
from packages.persistence.assembler import CANONICAL_FIELD_NAMES


def _content_hash(marker: str) -> str:
    return "sha256:" + hashlib.sha256(marker.encode("utf-8")).hexdigest()


def _execution_observation(
    *,
    observation_id: str = "obs_execution_spot_0001",
    source_event_id: str = "evt_execution_spot_0001",
    source_version: str = "1",
    content_marker: str = "v1",
    base_amount: str = "1000000.00",
    event_time: datetime | None = None,
) -> ExecutionObservation:
    event_time = event_time or datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
    effective_time = event_time + timedelta(seconds=0)
    ingest_time = event_time + timedelta(seconds=2)
    return ExecutionObservation(
        observation_id=observation_id,
        entity_version=1,
        tenant_id="tenant_demo",
        portfolio_id="portfolio_london",
        correlation_id="corr_ts10_spot_0001",
        source_event_id=source_event_id,
        source_business_key="trade_spot_0001",
        source_version=source_version,
        content_hash=_content_hash(content_marker),
        event_time=event_time,
        effective_time=effective_time,
        ingest_time=ingest_time,
        source_sequence=1,
        lineage_group_id="lineage_spot_0001",
        actor=Actor(identity_type="SOURCE", actor_id="fix_execution"),
        payload=ExecutionPayload(
            product_type="FX_SPOT",
            settlement_rule_version="1.0.0",
            source_trade_id="trade_spot_0001",
            base_currency="EUR",
            terms_currency="USD",
            side="BUY_BASE",
            base_amount=DecimalAmount(currency="EUR", value=base_amount, scale=2),
            terms_amount=DecimalAmount(currency="USD", value="1084500.00", scale=2),
            quoted_rate=DecimalRate(
                value="1.0845", scale=4, orientation="TERMS_CURRENCY_PER_BASE_CURRENCY"
            ),
            trade_date=event_time.date(),
            value_date=event_time.date() + timedelta(days=4),
            counterparty_id="cp_acme",
            book_id="book_london",
            lifecycle_status="NEW",
            execution_id="exec_spot_0001",
            execution_type="NEW",
            execution_status="EXECUTED",
            execution_time=event_time,
            order_id="order_spot_0001",
        ),
    )


def _confirmation_observation(
    *,
    observation_id: str = "obs_confirmation_spot_0001",
    source_event_id: str = "evt_confirmation_spot_0001",
    content_marker: str = "confirmation-v1",
    event_time: datetime | None = None,
) -> ConfirmationObservation:
    event_time = event_time or datetime(2026, 8, 3, 9, 32, tzinfo=UTC)
    return ConfirmationObservation(
        observation_id=observation_id,
        entity_version=1,
        tenant_id="tenant_demo",
        portfolio_id="portfolio_london",
        correlation_id="corr_ts10_spot_0001",
        source_event_id=source_event_id,
        source_business_key="trade_spot_0001",
        source_version="1",
        content_hash=_content_hash(content_marker),
        event_time=event_time,
        effective_time=event_time,
        ingest_time=event_time + timedelta(seconds=2),
        source_sequence=4,
        lineage_group_id="lineage_spot_0001",
        actor=Actor(identity_type="SOURCE", actor_id="fpml_confirmation"),
        payload=ConfirmationPayload(
            product_type="FX_SPOT",
            settlement_rule_version="1.0.0",
            source_trade_id="trade_spot_0001",
            base_currency="EUR",
            terms_currency="USD",
            side="BUY_BASE",
            base_amount=DecimalAmount(currency="EUR", value="1000000.00", scale=2),
            terms_amount=DecimalAmount(currency="USD", value="1084500.00", scale=2),
            quoted_rate=DecimalRate(
                value="1.0845", scale=4, orientation="TERMS_CURRENCY_PER_BASE_CURRENCY"
            ),
            trade_date=event_time.date(),
            value_date=event_time.date() + timedelta(days=4),
            counterparty_id="cp_acme",
            book_id="book_london",
            lifecycle_status="CONFIRMED",
            confirmation_id="confirmation_spot_0001",
            confirmation_reference="CONF-SPOT-0001",
            confirmation_status="AFFIRMED",
            confirmation_time=event_time,
            fpml_profile="fpml-style-fx-v1",
        ),
    )


def test_new_observation_is_inserted() -> None:
    store = InboxStore()
    observation = _execution_observation()

    result = store.ingest(observation)

    assert result.outcome is IngestOutcome.INSERTED
    assert result.record.observation.observation_id == observation.observation_id
    assert len(store.all_records()) == 1


def test_replay_same_identity_version_same_content_is_idempotent() -> None:
    store = InboxStore()
    first = _execution_observation()
    store.ingest(first)

    # A literal retransmission of the exact same observation.
    replay = _execution_observation()
    result = store.ingest(replay)

    assert result.outcome is IngestOutcome.IDEMPOTENT_REPLAY
    assert result.record.observation.observation_id == first.observation_id
    assert len(store.all_records()) == 1


def test_replay_across_different_delivery_identity_is_still_idempotent() -> None:
    """Honey's clarification (2026-08-02T23:34): delivery identity
    (source_system, source_event_id) must NOT be the conflict key. A
    retransmission with a brand-new observation_id/source_event_id but the
    same source identity/version and content is still an idempotent
    replay, not a second row."""
    store = InboxStore()
    first = _execution_observation(
        observation_id="obs_execution_spot_0001_delivery_a",
        source_event_id="evt_execution_spot_0001_delivery_a",
    )
    store.ingest(first)

    retransmission = _execution_observation(
        observation_id="obs_execution_spot_0001_delivery_b",
        source_event_id="evt_execution_spot_0001_delivery_b",
    )
    result = store.ingest(retransmission)

    assert result.outcome is IngestOutcome.IDEMPOTENT_REPLAY
    assert result.record.observation.observation_id == first.observation_id
    assert len(store.all_records()) == 1


def test_late_arrival_new_source_version_is_preserved_alongside_prior() -> None:
    store = InboxStore()
    v1 = _execution_observation(source_version="1", content_marker="v1")
    v2 = _execution_observation(
        observation_id="obs_execution_spot_0001_v2",
        source_event_id="evt_execution_spot_0001_v2",
        source_version="2",
        content_marker="v2",
        event_time=datetime(2026, 8, 3, 11, 0, tzinfo=UTC),
    )

    store.ingest(v1)
    result = store.ingest(v2)

    assert result.outcome is IngestOutcome.INSERTED
    # No destructive overwrite: both versions remain independently stored.
    assert len(store.all_records()) == 2
    assert store.get(identity_key(v1)) is not None
    assert store.get(identity_key(v2)) is not None


def test_same_identity_version_different_content_raises_duplicate_source_conflict() -> None:
    store = InboxStore()
    original = _execution_observation(content_marker="v1")
    store.ingest(original)

    conflicting = _execution_observation(
        observation_id="obs_execution_spot_0001_conflict",
        source_event_id="evt_execution_spot_0001_conflict",
        content_marker="v1-tampered",
    )

    with pytest.raises(SourceConflictError) as excinfo:
        store.ingest(conflicting)

    conflict = excinfo.value.conflict
    assert conflict.conflict_type == "SAME_SOURCE_KEY_VERSION_CONTENT"
    assert conflict.source_business_key == "trade_spot_0001"
    assert conflict.source_version == "1"
    assert set(conflict.source_observation_ids) == {
        original.observation_id,
        conflicting.observation_id,
    }
    # The conflicting delivery must never be silently applied or duplicated.
    assert len(store.all_records()) == 1
    assert store.get(identity_key(original)).observation.content_hash == original.content_hash


def _single_source_selection(observation: ExecutionObservation) -> dict:
    return {field_name: observation for field_name in CANONICAL_FIELD_NAMES}


def test_assemble_canonical_state_builds_valid_versioned_projection() -> None:
    observation = _execution_observation()
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")

    state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=_single_source_selection(observation),
        correlation_id=observation.correlation_id,
        actor=actor,
    )

    assert state.trade_id == "trade_spot_0001"
    assert state.canonical_state_version == 1
    assert state.tenant_id == observation.tenant_id
    assert state.portfolio_id == observation.portfolio_id
    assert state.state.base_currency == "EUR"
    assert state.state.base_amount.value == "1000000.00"
    assert len(state.source_version_set) == 1
    assert state.source_version_set[0].observation_id == observation.observation_id
    assert state.source_watermark == observation.ingest_time
    for field_name in CANONICAL_FIELD_NAMES:
        provenance = getattr(state.field_provenance, field_name)
        assert provenance.source_observation_id == observation.observation_id
        assert provenance.conflict_status == "SELECTED"


def test_assemble_canonical_state_respects_explicit_per_field_authority() -> None:
    """ADR-001 field-level authority (Honey, 2026-08-02T23:38): execution
    is authoritative for economics, confirmation for its own
    status/content. The assembler must persist exactly the caller's
    selection per field — never imply that one observation wins every
    field — and each field's provenance must point at the observation
    that was actually selected for it."""
    execution = _execution_observation()
    confirmation = _confirmation_observation()
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")

    field_selection = _single_source_selection(execution)
    field_selection["lifecycle_status"] = confirmation

    state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=field_selection,
        correlation_id=execution.correlation_id,
        actor=actor,
    )

    # lifecycle_status came from confirmation (CONFIRMED), not execution (NEW).
    assert state.state.lifecycle_status == "CONFIRMED"
    assert state.field_provenance.lifecycle_status.source_observation_id == (
        confirmation.observation_id
    )
    assert state.field_provenance.lifecycle_status.source_type == "CONFIRMATION"
    # Economics remain attributed to execution.
    assert state.field_provenance.base_amount.source_observation_id == execution.observation_id
    assert state.field_provenance.base_amount.source_type == "EXECUTION"
    # Both contributing observations are recorded in the source_version_set.
    contributing_ids = {item.observation_id for item in state.source_version_set}
    assert contributing_ids == {execution.observation_id, confirmation.observation_id}


def test_assemble_canonical_state_requires_a_complete_field_selection() -> None:
    execution = _execution_observation()
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")
    incomplete_selection = _single_source_selection(execution)
    del incomplete_selection["lifecycle_status"]

    with pytest.raises(KeyError):
        assemble_canonical_state(
            trade_id="trade_spot_0001",
            canonical_state_version=1,
            field_selection=incomplete_selection,
            correlation_id=execution.correlation_id,
            actor=actor,
        )


def test_versions_are_append_only_no_destructive_overwrite() -> None:
    """Late-arrival/correction acceptance criterion: every prior
    canonical_state_version remains available; the assembler never
    produces an update-in-place, only the next version."""
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")

    v1_observation = _execution_observation(base_amount="1000000.00")
    v1_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=_single_source_selection(v1_observation),
        correlation_id=v1_observation.correlation_id,
        actor=actor,
    )

    # A correction: same trade, revised economic terms, later ingest time.
    v2_observation = _execution_observation(
        observation_id="obs_execution_spot_0001_correction",
        source_event_id="evt_execution_spot_0001_correction",
        source_version="2",
        content_marker="corrected",
        base_amount="1000500.00",
        event_time=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )
    v2_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=2,
        field_selection=_single_source_selection(v2_observation),
        correlation_id=v2_observation.correlation_id,
        actor=actor,
    )

    # Simulated append-only store: an ordinary list, never mutated in place.
    canonical_trade_state_versions: list = [v1_state, v2_state]

    assert v1_state.canonical_state_version == 1
    assert v2_state.canonical_state_version == 2
    assert v1_state.state.base_amount.value == "1000000.00"
    assert v2_state.state.base_amount.value == "1000500.00"
    assert v1_state.content_hash != v2_state.content_hash
    # v1 is still present, byte-identical, after v2 is appended.
    assert canonical_trade_state_versions[0] is v1_state
    assert canonical_trade_state_versions[0].state.base_amount.value == "1000000.00"
