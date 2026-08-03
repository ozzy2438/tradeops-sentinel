"""TS-11 deterministic reconciliation tests for both FX products."""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from packages.contracts import compute_observation_content_hash
from packages.contracts.models import (
    Actor,
    BookingObservation,
    CandidateLink,
    ConfirmationObservation,
    ExecutionObservation,
    LinkageDecision,
    TradeCaptureObservation,
)
from packages.generator import generate_corpus
from packages.persistence import (
    assemble_canonical_state,
    load_mvp_source_of_truth_policy,
    resolve_field_selection,
)
from packages.reconciliation import (
    ChangedField,
    PostActionVerification,
    ReconciliationConfig,
    ReconciliationContext,
    ReconciliationEngine,
    ReconciliationRun,
    fixture_config,
)

_OBSERVATION_MODELS: dict[str, Any] = {
    "EXECUTION": ExecutionObservation,
    "TRADE_CAPTURE": TradeCaptureObservation,
    "CONFIRMATION": ConfirmationObservation,
    "BOOKING": BookingObservation,
}
_DATA_ROOT = Path(__file__).parents[1] / "packages" / "reconciliation" / "evidence"
_NEGATIVE_FAMILIES = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
)


@pytest.fixture(scope="module")
def corpus() -> Any:
    return generate_corpus()


def _truth(corpus: Any, *, product: str, family: str | None) -> dict[str, Any]:
    return next(
        item
        for item in corpus.truth_ledger
        if item["product_type"] == product and item["break_family"] == family
    )


def _raws_for_truth(corpus: Any, truth: dict[str, Any]) -> list[dict[str, Any]]:
    by_lineage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in corpus.source_observations:
        by_lineage[raw["lineage_group_id"]].append(raw)
    return copy.deepcopy(by_lineage[truth["lineage_group_id"]])


def _recompute_observation_hash(raw: dict[str, Any]) -> None:
    raw["content_hash"] = compute_observation_content_hash(raw)


def _build_context(
    raws: list[dict[str, Any]],
    *,
    run_id: str,
    family: str | None = None,
) -> tuple[ReconciliationContext, dict[str, Any]]:
    observations = tuple(
        _OBSERVATION_MODELS[raw["observation_kind"]].model_validate(raw) for raw in raws
    )
    baseline = observations[0]
    by_kind = {observation.observation_kind: observation for observation in observations}
    trade_id = Counter(item.source_business_key for item in observations).most_common(1)[0][0]
    policy = load_mvp_source_of_truth_policy()
    selection = resolve_field_selection(
        trade_id=trade_id,
        source_observations=observations,
        source_of_truth_policy=policy,
    )
    canonical = assemble_canonical_state(
        trade_id=trade_id,
        canonical_state_version=1,
        field_selection=selection,
        source_observations=observations,
        source_of_truth_policy=policy,
        correlation_id=baseline.correlation_id,
        actor=Actor(identity_type="SYSTEM", actor_id="ts11_fixture_assembler"),
    )

    is_ambiguous = family == "AMBIGUOUS_OR_UNMATCHED_LINKAGE" and len(observations) > 4
    is_unmatched = family == "AMBIGUOUS_OR_UNMATCHED_LINKAGE" and any(
        item.source_business_key != trade_id for item in observations
    )
    if is_ambiguous:
        decision = LinkageDecision(
            decision_id=f"decision_{run_id}",
            entity_version=1,
            tenant_id=canonical.tenant_id,
            portfolio_id=canonical.portfolio_id,
            correlation_id=canonical.correlation_id,
            content_hash="sha256:" + "1" * 64,
            created_at=canonical.source_watermark,
            actor=Actor(identity_type="SYSTEM", actor_id="linkage_engine"),
            source_observation_id=observations[0].observation_id,
            deterministic_rule_version="1.0.0",
            decision="AMBIGUOUS",
            candidate_links=[
                CandidateLink(
                    trade_id=trade_id,
                    tenant_id=canonical.tenant_id,
                    portfolio_id=canonical.portfolio_id,
                    match_key="match_key_001",
                    match_rule_version="1.0.0",
                    evidence_hash="sha256:" + "2" * 64,
                ),
                CandidateLink(
                    trade_id="trade_alternate_001",
                    tenant_id=canonical.tenant_id,
                    portfolio_id=canonical.portfolio_id,
                    match_key="match_key_002",
                    match_rule_version="1.0.0",
                    evidence_hash="sha256:" + "3" * 64,
                ),
            ],
            chosen_trade_id=None,
            reason_code="MULTIPLE_ELIGIBLE_CANDIDATES",
        )
    elif is_unmatched:
        decision = LinkageDecision(
            decision_id=f"decision_{run_id}",
            entity_version=1,
            tenant_id=canonical.tenant_id,
            portfolio_id=canonical.portfolio_id,
            correlation_id=canonical.correlation_id,
            content_hash="sha256:" + "1" * 64,
            created_at=canonical.source_watermark,
            actor=Actor(identity_type="SYSTEM", actor_id="linkage_engine"),
            source_observation_id=observations[0].observation_id,
            deterministic_rule_version="1.0.0",
            decision="UNMATCHED",
            candidate_links=[],
            chosen_trade_id=None,
            reason_code="NO_ELIGIBLE_CANDIDATE",
        )
    else:
        decision = LinkageDecision(
            decision_id=f"decision_{run_id}",
            entity_version=1,
            tenant_id=canonical.tenant_id,
            portfolio_id=canonical.portfolio_id,
            correlation_id=canonical.correlation_id,
            content_hash="sha256:" + "1" * 64,
            created_at=canonical.source_watermark,
            actor=Actor(identity_type="SYSTEM", actor_id="linkage_engine"),
            source_observation_id=observations[0].observation_id,
            deterministic_rule_version="1.0.0",
            decision="ACCEPTED",
            candidate_links=[
                CandidateLink(
                    trade_id=trade_id,
                    tenant_id=canonical.tenant_id,
                    portfolio_id=canonical.portfolio_id,
                    match_key="match_key_001",
                    match_rule_version="1.0.0",
                    evidence_hash="sha256:" + "2" * 64,
                )
            ],
            chosen_trade_id=trade_id,
            reason_code="EXACT_DETERMINISTIC_KEY",
        )
    context = ReconciliationContext(
        reconciliation_run_id=run_id,
        run_version=1,
        canonical_state=canonical,
        source_observations=observations,
        linkage_decision=decision,
    )
    return context, by_kind


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_clean_spot_and_forward_lifecycles_pass(corpus: Any, product: str) -> None:
    truth = _truth(corpus, product=product, family=None)
    context, _ = _build_context(
        _raws_for_truth(corpus, truth),
        run_id=f"run_clean_{product.lower()}",
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert result.result == "PASS"
    assert result.breaks == ()
    assert result.config_hash == fixture_config().content_hash


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_mixed_terminal_lifecycle_statuses_fail_closed(corpus: Any, product: str) -> None:
    truth = _truth(corpus, product=product, family=None)
    raws = _raws_for_truth(corpus, truth)
    terminal_updates = {
        "EXECUTION": {
            "execution_type": "CANCEL",
            "execution_status": "CANCELLED",
            "lifecycle_status": "CANCELLED",
        },
        "TRADE_CAPTURE": {
            "capture_type": "AMEND",
            "capture_status": "AMENDED",
            "lifecycle_status": "AMENDED",
        },
        "CONFIRMATION": {
            "confirmation_status": "CANCELLED",
            "lifecycle_status": "CANCELLED",
        },
        "BOOKING": {
            "booking_status": "AMENDED",
            "lifecycle_status": "AMENDED",
        },
    }
    for raw in raws:
        raw["payload"].update(terminal_updates[raw["observation_kind"]])
        _recompute_observation_hash(raw)

    context, _ = _build_context(
        raws,
        run_id=f"run_mixed_terminal_{product.lower()}",
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert result.result == "BREAKS_DETECTED"
    assert [item.family for item in result.breaks] == ["LIFECYCLE_STATUS_MISMATCH"]
    comparison = result.breaks[0].comparisons[0]
    assert {comparison.expected_value, comparison.observed_value} == {"AMENDED", "CANCELLED"}


@pytest.mark.parametrize("family", _NEGATIVE_FAMILIES)
@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_each_data_break_family_is_typed_and_scoped(
    corpus: Any,
    family: str,
    product: str,
) -> None:
    truth = _truth(corpus, product=product, family=family)
    context, _ = _build_context(
        _raws_for_truth(corpus, truth),
        run_id=f"run_{family.lower()}_{product.lower()}",
        family=family,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert result.result == "BREAKS_DETECTED"
    assert len(result.breaks) >= 1
    matching = [break_item for break_item in result.breaks if break_item.family == family]
    assert len(matching) == 1
    break_item = matching[0]
    assert break_item.state == "OPEN"
    assert break_item.previous_state is None
    assert break_item.transition_reason == "DETECTED"
    assert break_item.tenant_id == context.canonical_state.tenant_id
    assert break_item.portfolio_id == context.canonical_state.portfolio_id
    assert break_item.reconciliation_run_id == context.reconciliation_run_id
    assert result.break_facts


def test_reconciliation_is_repeatable_for_the_same_input(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_FORWARD", family="ECONOMIC_VALUE_MISMATCH")
    context, _ = _build_context(
        _raws_for_truth(corpus, truth),
        run_id="run_repeatable_economic_001",
        family="ECONOMIC_VALUE_MISMATCH",
    )
    engine = ReconciliationEngine(fixture_config())

    first = engine.run(context)
    second = engine.run(context)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_decimal_tolerance_boundary_is_accepted(corpus: Any, product: str) -> None:
    truth = _truth(corpus, product=product, family=None)
    raws = _raws_for_truth(corpus, truth)
    booking = next(raw for raw in raws if raw["observation_kind"] == "BOOKING")
    original_value = Decimal(booking["payload"]["base_amount"]["value"])
    booking["payload"]["base_amount"]["value"] = f"{original_value + Decimal('0.01'):.2f}"
    _recompute_observation_hash(booking)
    context, _ = _build_context(raws, run_id=f"run_decimal_boundary_{product.lower()}")

    result = ReconciliationEngine(fixture_config()).run(context)

    assert all(item.family != "ECONOMIC_VALUE_MISMATCH" for item in result.breaks)


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_missing_source_arrival_window_boundary_is_deterministic(
    corpus: Any,
    product: str,
) -> None:
    truth = _truth(corpus, product=product, family="MISSING_REQUIRED_SOURCE")
    context, by_kind = _build_context(
        _raws_for_truth(corpus, truth),
        run_id=f"run_missing_window_{product.lower()}",
        family="MISSING_REQUIRED_SOURCE",
    )
    missing_kind = next(
        kind for kind in ("EXECUTION", "CONFIRMATION", "BOOKING") if kind not in by_kind
    )
    config_data = fixture_config().model_dump(mode="python")
    for rule in config_data["arrival_windows"]:
        if rule["product_type"] == product and rule["observation_kind"] == missing_kind:
            rule["window_seconds"] = 60
    config = ReconciliationConfig(**config_data)
    before_window = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        evaluated_at=context.canonical_state.source_watermark + timedelta(seconds=59),
        linkage_decision=context.linkage_decision,
    )
    at_window = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        evaluated_at=context.canonical_state.source_watermark + timedelta(seconds=60),
        linkage_decision=context.linkage_decision,
    )

    assert ReconciliationEngine(config).run(before_window).result == "PASS"
    at_result = ReconciliationEngine(config).run(at_window)
    assert [item.family for item in at_result.breaks] == ["MISSING_REQUIRED_SOURCE"]

    after_window = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        evaluated_at=context.canonical_state.source_watermark + timedelta(seconds=61),
        linkage_decision=context.linkage_decision,
    )
    overdue_result = ReconciliationEngine(config).run(after_window)
    assert overdue_result.breaks[0].priority.deadline_status == "OVERDUE"


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_field_provenance_controls_the_expected_operand(corpus: Any, product: str) -> None:
    truth = _truth(corpus, product=product, family=None)
    raws = _raws_for_truth(corpus, truth)
    execution = next(raw for raw in raws if raw["observation_kind"] == "EXECUTION")
    execution["payload"]["base_amount"]["value"] = str(
        (Decimal(execution["payload"]["base_amount"]["value"]) + Decimal("2.00")).quantize(
            Decimal("0.01")
        )
    )
    _recompute_observation_hash(execution)
    context, by_kind = _build_context(
        raws,
        run_id=f"run_provenance_{product.lower()}",
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    economic_break = next(
        item for item in result.breaks if item.family == "ECONOMIC_VALUE_MISMATCH"
    )
    comparison = next(
        item for item in economic_break.comparisons if item.field_path == "/payload/base_amount"
    )
    assert comparison.expected_source_observation_id == by_kind["EXECUTION"].observation_id
    assert comparison.observed_source_observation_id == by_kind["TRADE_CAPTURE"].observation_id


def test_reconciliation_run_hash_is_self_validating(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    context, _ = _build_context(_raws_for_truth(corpus, truth), run_id="run_hash_validation_001")
    run = ReconciliationEngine(fixture_config()).run(context)
    tampered = run.model_dump(mode="json")
    tampered["content_hash"] = "sha256:" + "0" * 64

    with pytest.raises(ValidationError, match="content_hash"):
        ReconciliationRun.model_validate(tampered)


def test_same_content_replay_does_not_become_duplicate_conflict(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_FORWARD", family=None)
    raws = _raws_for_truth(corpus, truth)
    replay = copy.deepcopy(next(raw for raw in raws if raw["observation_kind"] == "BOOKING"))
    replay["observation_id"] = "obs_booking_replay_ts11_001"
    replay["source_event_id"] = "evt_booking_replay_ts11_001"
    raws.append(replay)
    context, _ = _build_context(raws, run_id="run_replay_001")

    result = ReconciliationEngine(fixture_config()).run(context)

    assert all(item.family != "DUPLICATE_SOURCE_CONFLICT" for item in result.breaks)


def test_cross_scope_source_is_rejected_before_evaluation(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    clean_context, _ = _build_context(_raws_for_truth(corpus, truth), run_id="run_cross_scope_001")
    raw = clean_context.source_observations[0].model_dump(mode="json")
    raw["tenant_id"] = "tenant_other"
    foreign_observation = _OBSERVATION_MODELS[raw["observation_kind"]].model_validate(raw)
    observations = list(clean_context.source_observations)
    observations[0] = foreign_observation

    with pytest.raises(ValidationError, match="source_observations"):
        ReconciliationContext(
            reconciliation_run_id=clean_context.reconciliation_run_id,
            run_version=clean_context.run_version,
            canonical_state=clean_context.canonical_state,
            source_observations=tuple(observations),
            linkage_decision=clean_context.linkage_decision,
        )


def test_source_after_watermark_is_rejected(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_FORWARD", family=None)
    clean_context, _ = _build_context(
        _raws_for_truth(corpus, truth), run_id="run_after_watermark_001"
    )
    raw = clean_context.source_observations[0].model_dump(mode="json")
    original_ingest = clean_context.source_observations[0].ingest_time
    raw["ingest_time"] = (original_ingest + timedelta(days=1)).isoformat()
    late_observation = _OBSERVATION_MODELS[raw["observation_kind"]].model_validate(raw)
    observations = list(clean_context.source_observations)
    observations[0] = late_observation

    with pytest.raises(ValidationError, match="after source_watermark"):
        ReconciliationContext(
            reconciliation_run_id=clean_context.reconciliation_run_id,
            run_version=clean_context.run_version,
            canonical_state=clean_context.canonical_state,
            source_observations=tuple(observations),
            linkage_decision=clean_context.linkage_decision,
        )


def test_invalid_config_is_rejected_and_fixture_is_not_operationally_approved() -> None:
    config = fixture_config()
    assert config.approval_status == "FIXTURE_ONLY"
    assert config.approval_reference is None
    assert len(config.arrival_windows) == 6
    assert len(config.decimal_tolerances) == 6
    assert config.detection_rule_version == "1.0.0"
    assert {
        rule.observation_kind: rule.expected_status for rule in config.lifecycle_expected_statuses
    } == {
        "EXECUTION": "NEW",
        "TRADE_CAPTURE": "CAPTURED",
        "CONFIRMATION": "CONFIRMED",
        "BOOKING": "BOOKED",
    }

    data = config.model_dump(mode="python")
    data["arrival_windows"] = data["arrival_windows"][:-1]
    with pytest.raises(ValidationError, match="arrival_windows"):
        ReconciliationConfig(**data)

    approved_data = config.model_dump(mode="python")
    approved_data["approval_status"] = "OWNER_APPROVED"
    with pytest.raises(ValidationError, match="approval_reference"):
        ReconciliationConfig(**approved_data)


def test_reconciliation_rejects_a_config_outside_its_effective_window(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    context, _ = _build_context(_raws_for_truth(corpus, truth), run_id="run_config_window_001")
    data = fixture_config().model_dump(mode="python")
    data["effective_from"] = context.effective_evaluated_at + timedelta(days=1)
    future_config = ReconciliationConfig(**data)

    with pytest.raises(ValueError, match="not effective"):
        ReconciliationEngine(future_config).run(context)


def test_committed_fixture_config_and_rule_matrix_are_machine_readable() -> None:
    config_document = json.loads((_DATA_ROOT / "fixture-config.json").read_text())
    committed_hash = config_document.pop("content_hash")
    config = ReconciliationConfig.model_validate(config_document)
    assert config.content_hash == committed_hash

    matrix = json.loads((_DATA_ROOT / "rule-matrix.json").read_text())
    assert len(matrix["families"]) == 8
    assert {family["product_coverage"][0] for family in matrix["families"]} == {"FX_SPOT"}
    assert all(
        set(family["product_coverage"]) == {"FX_SPOT", "FX_FORWARD"}
        and set(family["test_cases"]) == {"positive", "boundary", "negative"}
        for family in matrix["families"]
    )


def test_post_action_changed_field_is_readback_failure(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    raws = _raws_for_truth(corpus, truth)
    pre_raw = next(raw for raw in raws if raw["observation_kind"] == "BOOKING")
    post_raw = copy.deepcopy(pre_raw)
    post_raw["observation_id"] = "obs_booking_post_action_ts11_001"
    post_raw["source_event_id"] = "evt_booking_post_action_ts11_001"
    post_raw["source_version"] = "2"
    post_raw["source_sequence"] = 5
    post_raw["ingest_time"] = (
        _OBSERVATION_MODELS["BOOKING"].model_validate(pre_raw).ingest_time + timedelta(minutes=1)
    ).isoformat()
    post_raw["payload"]["book_id"] = "book_post_action_001"
    post_raw["payload"]["booking_version"] = 2
    post_raw["payload"]["record_fingerprint"] = "sha256:" + "4" * 64
    _recompute_observation_hash(post_raw)
    raws = [raw for raw in raws if raw["observation_kind"] != "BOOKING"]
    raws.extend([pre_raw, post_raw])
    context, _ = _build_context(raws, run_id="run_post_action_001")
    pre_action = next(
        observation
        for observation in context.source_observations
        if observation.observation_id == pre_raw["observation_id"]
    )
    post_action = next(
        observation
        for observation in context.source_observations
        if observation.observation_id == post_raw["observation_id"]
    )
    assert isinstance(pre_action, BookingObservation)
    assert isinstance(post_action, BookingObservation)
    verification = PostActionVerification(
        action_instruction_hash="sha256:" + "5" * 64,
        pre_action=pre_action,
        post_action=post_action,
        changed_fields=(
            ChangedField(
                field_path="/payload/book_id",
                expected_value=pre_action.payload.book_id,
                observed_value=post_action.payload.book_id,
            ),
        ),
    )
    context = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=verification,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert [item.family for item in result.breaks] == ["POST_ACTION_VERIFICATION_FAILURE"]
    assert {item.role for item in result.breaks[0].evidence} == {
        "ACTION_INSTRUCTION",
        "PRE_ACTION_READ",
        "POST_ACTION_READ",
        "CHANGED_FIELD_DIFF",
        "RECONCILIATION_RESULT",
    }


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_post_action_booking_version_and_fingerprint_drift_is_a_break(
    corpus: Any,
    product: str,
) -> None:
    truth = _truth(corpus, product=product, family=None)
    raws = _raws_for_truth(corpus, truth)
    pre_raw = next(raw for raw in raws if raw["observation_kind"] == "BOOKING")
    post_raw = copy.deepcopy(pre_raw)
    post_raw["observation_id"] = f"obs_booking_post_action_drift_{product.lower()}"
    post_raw["source_event_id"] = f"evt_booking_post_action_drift_{product.lower()}"
    post_raw["source_version"] = "2"
    post_raw["source_sequence"] = 5
    post_raw["ingest_time"] = (
        _OBSERVATION_MODELS["BOOKING"].model_validate(pre_raw).ingest_time + timedelta(minutes=1)
    ).isoformat()
    post_raw["payload"]["booking_version"] = 2
    post_raw["payload"]["record_fingerprint"] = "sha256:" + "9" * 64
    _recompute_observation_hash(post_raw)
    raws = [raw for raw in raws if raw["observation_kind"] != "BOOKING"]
    raws.extend([pre_raw, post_raw])
    context, _ = _build_context(
        raws,
        run_id=f"run_post_action_drift_{product.lower()}",
    )
    pre_action = next(
        observation
        for observation in context.source_observations
        if observation.observation_id == pre_raw["observation_id"]
    )
    post_action = next(
        observation
        for observation in context.source_observations
        if observation.observation_id == post_raw["observation_id"]
    )
    assert isinstance(pre_action, BookingObservation)
    assert isinstance(post_action, BookingObservation)
    verification = PostActionVerification(
        action_instruction_hash="sha256:" + "9" * 64,
        pre_action=pre_action,
        post_action=post_action,
        changed_fields=(),
    )
    context = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=verification,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert [item.family for item in result.breaks] == ["POST_ACTION_VERIFICATION_FAILURE"]
    assert {comparison.field_path for comparison in result.breaks[0].comparisons} == {
        "/payload/booking_version",
        "/payload/record_fingerprint",
    }
    assert {comparison.value_type for comparison in result.breaks[0].comparisons} == {
        "SOURCE_VERSION",
        "CONTENT_HASH",
    }
    assert {
        evidence.field_path
        for evidence in result.breaks[0].evidence
        if evidence.role == "CHANGED_FIELD_DIFF"
    } == {
        "/payload/booking_version",
        "/payload/record_fingerprint",
    }


def test_post_action_unavailable_readback_is_a_break(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_FORWARD", family=None)
    context, by_kind = _build_context(
        _raws_for_truth(corpus, truth), run_id="run_post_action_unavailable_001"
    )
    booking = by_kind["BOOKING"]
    assert isinstance(booking, BookingObservation)
    verification = PostActionVerification(
        action_instruction_hash="sha256:" + "6" * 64,
        pre_action=booking,
        readback_available=False,
    )
    context = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=verification,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert [item.family for item in result.breaks] == ["POST_ACTION_VERIFICATION_FAILURE"]


def test_post_action_unavailable_readback_with_post_action_is_a_break(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    context, by_kind = _build_context(
        _raws_for_truth(corpus, truth), run_id="run_post_action_unavailable_present_001"
    )
    booking = by_kind["BOOKING"]
    assert isinstance(booking, BookingObservation)
    verification = PostActionVerification(
        action_instruction_hash="sha256:" + "8" * 64,
        pre_action=booking,
        post_action=booking,
        readback_available=False,
    )
    context = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=verification,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert [item.family for item in result.breaks] == ["POST_ACTION_VERIFICATION_FAILURE"]


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
def test_post_action_verified_readback_is_not_a_break(corpus: Any, product: str) -> None:
    truth = _truth(corpus, product=product, family=None)
    context, by_kind = _build_context(
        _raws_for_truth(corpus, truth), run_id=f"run_post_action_pass_{product.lower()}"
    )
    booking = by_kind["BOOKING"]
    assert isinstance(booking, BookingObservation)
    verification = PostActionVerification(
        action_instruction_hash="sha256:" + "7" * 64,
        pre_action=booking,
        post_action=booking,
        readback_available=True,
    )
    context = ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=verification,
    )

    result = ReconciliationEngine(fixture_config()).run(context)

    assert all(item.family != "POST_ACTION_VERIFICATION_FAILURE" for item in result.breaks)
