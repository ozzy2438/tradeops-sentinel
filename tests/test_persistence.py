"""TS-10 tests: source_event_inbox ingest decisions + canonical assembler.

Required tests per issue #10: replay, late-arrival, duplicate-vs-conflict.
Also covers Honey's identity-vs-delivery clarification (2026-08-02T23:34)
and the append-only "no destructive overwrite" acceptance criterion.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts import (
    ObservationContentHashMismatchError,
    compute_observation_content_hash,
)
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
    SourceObservationSetError,
    SourceOfTruthPolicyContentError,
    SourceOfTruthPolicyVersionError,
    SourceOfTruthSelectionError,
    assemble_canonical_state,
    identity_key,
    load_mvp_source_of_truth_policy,
    resolve_field_selection,
)
from packages.persistence.assembler import CANONICAL_FIELD_NAMES


def _with_content_hash(observation: ExecutionObservation) -> ExecutionObservation:
    return observation.model_copy(
        update={"content_hash": compute_observation_content_hash(observation)}
    )


def _execution_observation(
    *,
    observation_id: str = "obs_execution_spot_0001",
    source_event_id: str = "evt_execution_spot_0001",
    source_version: str = "1",
    base_amount: str = "1000000.00",
    event_time: datetime | None = None,
    ingest_time: datetime | None = None,
    supersedes_observation_id: str | None = None,
    supersession_reason: str | None = None,
) -> ExecutionObservation:
    event_time = event_time or datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
    effective_time = event_time + timedelta(seconds=0)
    ingest_time = ingest_time or (event_time + timedelta(seconds=2))
    observation = ExecutionObservation(
        observation_id=observation_id,
        entity_version=1,
        tenant_id="tenant_demo",
        portfolio_id="portfolio_london",
        correlation_id="corr_ts10_spot_0001",
        source_event_id=source_event_id,
        source_business_key="trade_spot_0001",
        source_version=source_version,
        content_hash="sha256:" + "0" * 64,
        event_time=event_time,
        effective_time=effective_time,
        ingest_time=ingest_time,
        source_sequence=1,
        lineage_group_id="lineage_spot_0001",
        supersedes_observation_id=supersedes_observation_id,
        supersession_reason=supersession_reason,
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
    return _with_content_hash(observation)


def _confirmation_observation(
    *,
    observation_id: str = "obs_confirmation_spot_0001",
    source_event_id: str = "evt_confirmation_spot_0001",
    event_time: datetime | None = None,
) -> ConfirmationObservation:
    event_time = event_time or datetime(2026, 8, 3, 9, 32, tzinfo=UTC)
    observation = ConfirmationObservation(
        observation_id=observation_id,
        entity_version=1,
        tenant_id="tenant_demo",
        portfolio_id="portfolio_london",
        correlation_id="corr_ts10_spot_0001",
        source_event_id=source_event_id,
        source_business_key="trade_spot_0001",
        source_version="1",
        content_hash="sha256:" + "0" * 64,
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
    return observation.model_copy(
        update={"content_hash": compute_observation_content_hash(observation)}
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
    """True late-arrival case (Honey, 2026-08-02T23:52 and 2026-08-03T00:00):
    the newer logical revision (source_version=2) is ingested FIRST, then
    the older logical revision (source_version=1) arrives LATE -- its
    ingest_time is well after v2's ingest_time despite its event_time
    being earlier. Both remain independently stored regardless of
    arrival order; the inbox does not require increasing ingest-time
    order across distinct identity/version keys."""
    store = InboxStore()
    v2 = _execution_observation(
        observation_id="obs_execution_spot_0001_v2",
        source_event_id="evt_execution_spot_0001_v2",
        source_version="2",
        event_time=datetime(2026, 8, 3, 11, 0, tzinfo=UTC),
    )
    v1_late = _execution_observation(
        source_version="1",
        event_time=datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
        ingest_time=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )

    v2_result = store.ingest(v2)
    v1_result = store.ingest(v1_late)

    assert v2_result.outcome is IngestOutcome.INSERTED
    assert v1_result.outcome is IngestOutcome.INSERTED
    # v1 genuinely arrived late: its event_time precedes v2's, but its
    # ingest_time is after v2's ingest_time.
    assert v1_late.event_time < v2.event_time
    assert v1_late.ingest_time > v2.ingest_time
    # No destructive overwrite: both versions remain independently stored,
    # regardless of arrival order.
    assert len(store.all_records()) == 2
    assert store.get(identity_key(v1_late)) is not None
    assert store.get(identity_key(v2)) is not None

    # Both versions can still be assembled and appended without either
    # replacing the other, even though v1 was processed after v2.
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")
    v2_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=_single_source_selection(v2),
        source_observations=(v2,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=v2.correlation_id,
        actor=actor,
    )
    v1_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=2,
        field_selection=_single_source_selection(v1_late),
        source_observations=(v1_late,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=v1_late.correlation_id,
        actor=actor,
    )
    canonical_trade_state_versions: list = [v2_state, v1_state]
    assert canonical_trade_state_versions[0] is v2_state
    assert canonical_trade_state_versions[1] is v1_state
    assert v2_state.content_hash != v1_state.content_hash


def test_linked_correction_preserves_supersession_metadata_and_prior_version() -> None:
    """Supersession/correction case (Honey, 2026-08-02T23:52): a later
    observation explicitly supersedes an earlier one via
    supersedes_observation_id/supersession_reason. Both the original and
    the correction remain independently preserved in the inbox and as
    separate append-only canonical versions; the correction's supersession
    metadata is retrievable, not discarded."""
    store = InboxStore()
    original = _execution_observation(source_version="1")
    correction = _execution_observation(
        observation_id="obs_execution_spot_0001_correction",
        source_event_id="evt_execution_spot_0001_correction",
        source_version="2",
        base_amount="1000500.00",
        event_time=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        supersedes_observation_id=original.observation_id,
        supersession_reason="CORRECTION",
    )

    original_result = store.ingest(original)
    correction_result = store.ingest(correction)

    assert original_result.outcome is IngestOutcome.INSERTED
    assert correction_result.outcome is IngestOutcome.INSERTED
    assert len(store.all_records()) == 2

    stored_correction = store.get(identity_key(correction))
    assert stored_correction is not None
    assert stored_correction.observation.supersedes_observation_id == original.observation_id
    assert stored_correction.observation.supersession_reason == "CORRECTION"
    # The original observation the correction points at is itself still
    # present and untouched -- superseding never deletes or mutates it.
    stored_original = store.get(identity_key(original))
    assert stored_original is not None
    assert stored_original.observation.supersedes_observation_id is None

    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")
    original_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=_single_source_selection(original),
        source_observations=(original,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=original.correlation_id,
        actor=actor,
    )
    correction_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=2,
        field_selection=_single_source_selection(correction),
        source_observations=(correction,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=correction.correlation_id,
        actor=actor,
    )
    canonical_trade_state_versions: list = [original_state, correction_state]
    assert canonical_trade_state_versions[0].state.base_amount.value == "1000000.00"
    assert canonical_trade_state_versions[1].state.base_amount.value == "1000500.00"


def test_duplicate_observation_id_with_different_content_hash_is_rejected() -> None:
    """Ambiguous duplicate identity (Fizz, 2026-08-02T23:58; confirmed by
    Honey, 2026-08-03T00:00): two envelope objects sharing an
    observation_id but disagreeing on content_hash must never silently
    flow into state/field_provenance/source_version_set -- the assembler
    rejects it deterministically before projection."""
    canonical = _execution_observation()
    changed_payload = canonical.payload.model_copy(update={"book_id": "book_sydney"})
    tampered_without_hash = canonical.model_copy(update={"payload": changed_payload})
    tampered = tampered_without_hash.model_copy(
        update={"content_hash": compute_observation_content_hash(tampered_without_hash)}
    )
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")

    field_selection = _single_source_selection(canonical)
    field_selection["lifecycle_status"] = tampered

    with pytest.raises(ValueError, match="inconsistent envelopes"):
        assemble_canonical_state(
            trade_id="trade_spot_0001",
            canonical_state_version=1,
            field_selection=field_selection,
            source_observations=(canonical,),
            source_of_truth_policy=load_mvp_source_of_truth_policy(),
            correlation_id=canonical.correlation_id,
            actor=actor,
        )


def test_duplicate_observation_id_with_same_content_hash_remains_idempotent() -> None:
    """Same observation_id + same content_hash across selected fields is
    the normal single-source case, not an error."""
    canonical = _execution_observation()
    same_object_again = canonical.model_copy()
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")

    field_selection = _single_source_selection(canonical)
    field_selection["lifecycle_status"] = same_object_again

    state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=field_selection,
        source_observations=(canonical,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=canonical.correlation_id,
        actor=actor,
    )
    assert state.state.lifecycle_status == "NEW"


def test_same_identity_version_different_content_raises_duplicate_source_conflict() -> None:
    store = InboxStore()
    original = _execution_observation()
    store.ingest(original)

    conflicting = _execution_observation(
        observation_id="obs_execution_spot_0001_conflict",
        source_event_id="evt_execution_spot_0001_conflict",
        base_amount="1000500.00",
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
        source_observations=(observation,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
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
        expected_status = "SECONDARY_SUPPORTING" if field_name == "lifecycle_status" else "SELECTED"
        assert provenance.conflict_status == expected_status


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
        source_observations=(execution, confirmation),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
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


def test_source_of_truth_rejects_non_authoritative_economic_selection() -> None:
    execution = _execution_observation()
    confirmation = _confirmation_observation()
    policy = load_mvp_source_of_truth_policy()
    selection = resolve_field_selection(
        trade_id="trade_spot_0001",
        source_observations=(execution, confirmation),
        source_of_truth_policy=policy,
    )
    selection["base_amount"] = confirmation

    with pytest.raises(SourceOfTruthSelectionError, match="base_amount"):
        assemble_canonical_state(
            trade_id="trade_spot_0001",
            canonical_state_version=1,
            field_selection=selection,
            source_observations=(execution, confirmation),
            source_of_truth_policy=policy,
            correlation_id=execution.correlation_id,
            actor=Actor(identity_type="SYSTEM", actor_id="canonical_assembler"),
        )


def test_full_source_set_is_distinct_from_selected_authoritative_sources() -> None:
    prior_execution = _execution_observation()
    execution = _execution_observation(
        observation_id="obs_execution_spot_0001_v2",
        source_event_id="evt_execution_spot_0001_v2",
        source_version="2",
    )
    confirmation = _confirmation_observation()
    policy = load_mvp_source_of_truth_policy()
    selection = resolve_field_selection(
        trade_id="trade_spot_0001",
        source_observations=(confirmation, prior_execution, execution),
        source_of_truth_policy=policy,
    )

    state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=1,
        field_selection=selection,
        source_observations=(confirmation, prior_execution, execution),
        source_of_truth_policy=policy,
        correlation_id=execution.correlation_id,
        actor=Actor(identity_type="SYSTEM", actor_id="canonical_assembler"),
    )

    assert state.field_provenance.base_amount.source_observation_id == execution.observation_id
    assert state.field_provenance.lifecycle_status.source_observation_id == (
        confirmation.observation_id
    )
    assert {item.observation_id for item in state.source_version_set} == {
        execution.observation_id,
        prior_execution.observation_id,
        confirmation.observation_id,
    }
    assert prior_execution.observation_id not in {
        observation.observation_id for observation in selection.values()
    }


def test_source_set_rejects_cross_portfolio_observation() -> None:
    execution = _execution_observation()
    confirmation = _confirmation_observation()
    moved = confirmation.model_copy(update={"portfolio_id": "portfolio_sydney"})
    moved = moved.model_copy(update={"content_hash": compute_observation_content_hash(moved)})
    policy = load_mvp_source_of_truth_policy()

    with pytest.raises(SourceObservationSetError, match="tenant/portfolio/correlation"):
        resolve_field_selection(
            trade_id="trade_spot_0001",
            source_observations=(execution, moved),
            source_of_truth_policy=policy,
        )


def test_unsupported_source_of_truth_policy_version_fails_closed() -> None:
    observation = _execution_observation()
    unsupported = load_mvp_source_of_truth_policy().model_copy(update={"policy_version": "2.0.0"})

    with pytest.raises(SourceOfTruthPolicyVersionError, match="unsupported"):
        resolve_field_selection(
            trade_id="trade_spot_0001",
            source_observations=(observation,),
            source_of_truth_policy=unsupported,
        )


def test_same_version_source_of_truth_policy_tampering_fails_closed() -> None:
    observation = _execution_observation()
    policy = load_mvp_source_of_truth_policy()
    first_rule = policy.field_rules[0].model_copy(
        update={"source_precedence": list(reversed(policy.field_rules[0].source_precedence))}
    )
    tampered = policy.model_copy(update={"field_rules": [first_rule, *policy.field_rules[1:]]})

    with pytest.raises(SourceOfTruthPolicyContentError, match="approved policy"):
        resolve_field_selection(
            trade_id="trade_spot_0001",
            source_observations=(observation,),
            source_of_truth_policy=tampered,
        )


def test_forged_same_hash_cannot_hide_a_changed_payload() -> None:
    store = InboxStore()
    original = _execution_observation()
    store.ingest(original)
    changed_payload = original.payload.model_copy(
        update={"base_amount": DecimalAmount(currency="EUR", value="1000500.00", scale=2)}
    )
    forged = original.model_copy(
        update={
            "observation_id": "obs_execution_spot_0001_forged",
            "source_event_id": "evt_execution_spot_0001_forged",
            "payload": changed_payload,
            "content_hash": original.content_hash,
        }
    )

    with pytest.raises(ObservationContentHashMismatchError, match="canonical content"):
        store.ingest(forged)
    assert len(store.all_records()) == 1


def test_forged_selected_observation_cannot_bypass_policy_resolution() -> None:
    original = _execution_observation()
    policy = load_mvp_source_of_truth_policy()
    selection = resolve_field_selection(
        trade_id="trade_spot_0001",
        source_observations=(original,),
        source_of_truth_policy=policy,
    )
    changed_payload = original.payload.model_copy(
        update={"base_amount": DecimalAmount(currency="EUR", value="1000500.00", scale=2)}
    )
    selection["base_amount"] = original.model_copy(update={"payload": changed_payload})

    with pytest.raises(ObservationContentHashMismatchError, match="canonical content"):
        assemble_canonical_state(
            trade_id="trade_spot_0001",
            canonical_state_version=1,
            field_selection=selection,
            source_observations=(original,),
            source_of_truth_policy=policy,
            correlation_id=original.correlation_id,
            actor=Actor(identity_type="SYSTEM", actor_id="canonical_assembler"),
        )


def test_observation_hash_is_key_order_independent_and_normalises_utc_offsets() -> None:
    observation = _execution_observation()
    document = observation.model_dump(mode="json")
    equivalent = deepcopy(document)
    equivalent["event_time"] = "2026-08-03T19:30:00+10:00"
    equivalent["effective_time"] = "2026-08-03T19:30:00+10:00"
    equivalent["payload"]["execution_time"] = "2026-08-03T19:30:00+10:00"
    equivalent["payload"] = dict(reversed(equivalent["payload"].items()))
    equivalent = dict(reversed(equivalent.items()))

    assert compute_observation_content_hash(document) == compute_observation_content_hash(
        equivalent
    )


def test_observation_hash_preserves_declared_decimal_representation() -> None:
    original = _execution_observation()
    changed_payload = original.payload.model_copy(
        update={"base_amount": DecimalAmount(currency="EUR", value="1000000.01", scale=2)}
    )
    changed = original.model_copy(update={"payload": changed_payload})

    assert compute_observation_content_hash(original) != compute_observation_content_hash(changed)


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
            source_observations=(execution,),
            source_of_truth_policy=load_mvp_source_of_truth_policy(),
            correlation_id=execution.correlation_id,
            actor=actor,
        )


def test_assemble_canonical_state_rejects_unrecognised_extra_field_keys() -> None:
    """Fail-closed on the exact key set (Honey, 2026-08-02T23:46, finding
    3): an unknown extra key must be rejected, not silently accepted into
    source_version_set/source_watermark computation."""
    execution = _execution_observation()
    actor = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")
    selection_with_extra_key = _single_source_selection(execution)
    selection_with_extra_key["not_a_canonical_field"] = execution

    with pytest.raises(KeyError):
        assemble_canonical_state(
            trade_id="trade_spot_0001",
            canonical_state_version=1,
            field_selection=selection_with_extra_key,
            source_observations=(execution,),
            source_of_truth_policy=load_mvp_source_of_truth_policy(),
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
        source_observations=(v1_observation,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
        correlation_id=v1_observation.correlation_id,
        actor=actor,
    )

    # A correction: same trade, revised economic terms, later ingest time.
    v2_observation = _execution_observation(
        observation_id="obs_execution_spot_0001_correction",
        source_event_id="evt_execution_spot_0001_correction",
        source_version="2",
        base_amount="1000500.00",
        event_time=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )
    v2_state = assemble_canonical_state(
        trade_id="trade_spot_0001",
        canonical_state_version=2,
        field_selection=_single_source_selection(v2_observation),
        source_observations=(v2_observation,),
        source_of_truth_policy=load_mvp_source_of_truth_policy(),
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
