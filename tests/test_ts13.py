"""TS-13 rerun determinism and duplicate/conflict invariant tests."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from packages.contracts.models import (
    Actor,
    BookingObservation,
    ConfirmationObservation,
    ExecutionObservation,
    ObservationModel,
    TradeCaptureObservation,
)
from packages.generator import generate_corpus
from packages.persistence import InboxStore, IngestOutcome, SourceConflictError
from packages.persistence.assembler import CANONICAL_FIELD_NAMES, assemble_canonical_state
from packages.reconciliation import ReconciliationEngine, fixture_config
from packages.reconciliation.models import ReconciliationContext

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "packages/reconciliation/evidence/ts13-invariants.json"

_OBSERVATION_MODELS: dict[str, Any] = {
    "EXECUTION": ExecutionObservation,
    "TRADE_CAPTURE": TradeCaptureObservation,
    "CONFIRMATION": ConfirmationObservation,
    "BOOKING": BookingObservation,
}
_SAFE_EXTRA_FIELDS = (
    "lifecycle_status",
    "counterparty_id",
    "book_id",
    "settlement_rule_version",
    "product_type",
)


def _observation_sort_key(observation: ObservationModel) -> tuple[Any, ...]:
    return (
        observation.observation_kind,
        observation.source_sequence,
        observation.observation_id,
        observation.source_version,
        observation.content_hash,
    )


def _as_observations(raws: list[dict[str, Any]]) -> tuple[ObservationModel, ...]:
    observations = tuple(
        _OBSERVATION_MODELS[raw["observation_kind"]].model_validate(raw) for raw in raws
    )
    return tuple(sorted(observations, key=_observation_sort_key))


def _locked_context(raws: list[dict[str, Any]], run_id: str) -> ReconciliationContext:
    observations = _as_observations(raws)
    if not observations:
        raise AssertionError("TS-13 locked context requires at least one observation")
    if len(observations) > len(_SAFE_EXTRA_FIELDS) + 1:
        raise AssertionError("TS-13 fixture helper cannot represent this source-set width")

    anchor = observations[0]
    field_selection: dict[str, ObservationModel] = {
        field_name: anchor for field_name in CANONICAL_FIELD_NAMES
    }
    for observation, field_name in zip(observations[1:], _SAFE_EXTRA_FIELDS):
        field_selection[field_name] = observation

    canonical = assemble_canonical_state(
        trade_id=anchor.payload.source_trade_id,
        canonical_state_version=1,
        field_selection=field_selection,
        correlation_id=anchor.correlation_id,
        actor=Actor(identity_type="SYSTEM", actor_id="ts13_fixture_assembler"),
    )
    return ReconciliationContext(
        reconciliation_run_id=run_id,
        run_version=1,
        canonical_state=canonical,
        source_observations=observations,
        evaluated_at=canonical.source_watermark,
    )


def _lineage_groups(corpus: Any) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in corpus.source_observations:
        grouped[raw["lineage_group_id"]].append(raw)
    return [grouped[lineage_id] for lineage_id in sorted(grouped)]


def _duplicate_group_keys(
    observations: tuple[ObservationModel, ...],
) -> set[tuple[str, str, str]]:
    grouped: dict[tuple[str, str, str], list[ObservationModel]] = defaultdict(list)
    for observation in observations:
        grouped[
            (
                observation.observation_kind,
                observation.source_business_key,
                observation.source_version,
            )
        ].append(observation)
    return {
        key
        for key, group in grouped.items()
        if len({observation.content_hash for observation in group}) > 1
    }


def _content_hash(marker: str) -> str:
    return "sha256:" + hashlib.sha256(marker.encode("utf-8")).hexdigest()


def test_locked_corpus_reruns_are_identical_and_order_independent() -> None:
    corpus = generate_corpus()
    groups = _lineage_groups(corpus)
    engine = ReconciliationEngine(fixture_config())
    duplicate_group_count = 0

    assert len(groups) == 144
    for index, raws in enumerate(groups, start=1):
        context = _locked_context(raws, run_id=f"run_ts13_locked_{index:03d}")
        first = engine.run(context)
        second = engine.run(context)
        permuted = ReconciliationContext(
            reconciliation_run_id=context.reconciliation_run_id,
            run_version=context.run_version,
            canonical_state=context.canonical_state,
            source_observations=tuple(reversed(context.source_observations)),
            evaluated_at=context.evaluated_at,
        )
        reordered = engine.run(permuted)

        first_document = first.model_dump_json()
        assert second.model_dump_json() == first_document
        assert reordered.model_dump_json() == first_document
        assert len(first.break_ids) == len(set(first.break_ids))
        assert len(first.break_ids) == len(first.breaks)

        expected_duplicate_groups = _duplicate_group_keys(context.source_observations)
        actual_duplicate_groups = set()
        observations_by_id = {
            observation.observation_id: observation for observation in context.source_observations
        }
        duplicate_breaks = [
            item for item in first.breaks if item.family == "DUPLICATE_SOURCE_CONFLICT"
        ]
        for break_item in first.breaks:
            if break_item.family != "DUPLICATE_SOURCE_CONFLICT":
                continue
            conflict = break_item.duplicate_source_conflict
            assert conflict is not None
            source_ids = [item.source_observation_id for item in break_item.source_version_set]
            assert conflict.source_observation_ids == source_ids
            assert len(source_ids) == len(set(source_ids))
            assert (
                len({observations_by_id[source_id].content_hash for source_id in source_ids}) >= 2
            )
            kinds = {item.observation_kind for item in break_item.source_version_set}
            assert len(kinds) == 1
            actual_duplicate_groups.add(
                (next(iter(kinds)), conflict.source_business_key, conflict.source_version)
            )
        assert actual_duplicate_groups == expected_duplicate_groups
        assert len(duplicate_breaks) == len(actual_duplicate_groups)
        duplicate_group_count += len(actual_duplicate_groups)

    assert duplicate_group_count == 12


def test_duplicate_event_replay_is_idempotent_and_conflict_is_fail_closed() -> None:
    raw = next(
        raw
        for raw in generate_corpus().source_observations
        if raw["observation_kind"] == "EXECUTION"
    )
    original = _as_observations([raw])[0]
    replay = original.model_copy(
        update={
            "observation_id": "obs_ts13_replay_delivery",
            "source_event_id": "evt_ts13_replay_delivery",
        }
    )
    conflicting = original.model_copy(
        update={
            "observation_id": "obs_ts13_conflicting_delivery",
            "source_event_id": "evt_ts13_conflicting_delivery",
            "content_hash": _content_hash("ts13-conflicting-content"),
        }
    )

    store = InboxStore()
    inserted = store.ingest(original)
    replay_result = store.ingest(replay)
    assert inserted.outcome is IngestOutcome.INSERTED
    assert replay_result.outcome is IngestOutcome.IDEMPOTENT_REPLAY
    assert replay_result.record.observation.observation_id == original.observation_id
    assert len(store.all_records()) == 1

    engine = ReconciliationEngine(fixture_config())
    original_context = _locked_context([original.model_dump(mode="json")], "run_ts13_replay")
    original_run = engine.run(original_context)
    replay_context = _locked_context(
        [store.all_records()[0].observation.model_dump(mode="json")],
        "run_ts13_replay",
    )
    replay_run = engine.run(replay_context)
    assert replay_run.model_dump_json() == original_run.model_dump_json()
    assert all(item.family != "DUPLICATE_SOURCE_CONFLICT" for item in replay_run.breaks)

    with pytest.raises(SourceConflictError) as excinfo:
        store.ingest(conflicting)
    conflict = excinfo.value.conflict
    assert conflict.conflict_type == "SAME_SOURCE_KEY_VERSION_CONTENT"
    assert conflict.source_business_key == original.source_business_key
    assert conflict.source_version == original.source_version
    assert set(conflict.source_observation_ids) == {
        original.observation_id,
        conflicting.observation_id,
    }
    assert len(store.all_records()) == 1
    assert store.all_records()[0].observation.content_hash == original.content_hash

    conflict_run = engine.run(
        _locked_context(
            [original.model_dump(mode="json"), conflicting.model_dump(mode="json")],
            "run_ts13_conflict",
        )
    )
    conflict_breaks = [
        item for item in conflict_run.breaks if item.family == "DUPLICATE_SOURCE_CONFLICT"
    ]
    assert len(conflict_breaks) == 1
    assert conflict_breaks[0].duplicate_source_conflict is not None
    assert len(conflict_run.break_ids) == len(set(conflict_run.break_ids))


def test_committed_ts13_evidence_matches_locked_input_manifest() -> None:
    summary = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    corpus = generate_corpus()
    manifest = corpus.evidence_manifest

    assert summary["issue"] == 13
    assert summary["base_main_merge"] == "e7610d003daf2faa6bae067b2468657bc93cf95c"
    assert summary["locked_inputs"] == {
        "generator_version": manifest["generator_version"],
        "seed": manifest["seed"],
        "scenario_count": manifest["scenario_count"],
        "lineage_group_count": manifest["lineage_group_count"],
        "source_observation_count": manifest["source_observation_count"],
        "config_hash": manifest["config_hash"],
        "source_fixture_hash": manifest["source_fixture_hash"],
    }
    assert summary["rerun_determinism"]["lineage_groups"] == 144
    assert summary["rerun_determinism"]["identical_replays"] == 144
    assert summary["rerun_determinism"]["source_order_permutations"] == 144
    assert summary["duplicate_conflict"]["expected_groups"] == 12
    assert summary["duplicate_event"]["replay_outcome"] == "IDEMPOTENT_REPLAY"
    assert summary["duplicate_event"]["conflict_exception"] == "SourceConflictError"
    assert summary["evidence_files"]["tests"] == "tests/test_ts13.py"
    assert summary["evidence_files"]["summary"] == (
        "packages/reconciliation/evidence/ts13-invariants.json"
    )
