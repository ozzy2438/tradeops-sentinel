"""TS-12 independent-oracle parity and import-isolation tests."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

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
from packages.oracle import evaluate
from packages.oracle.import_graph import ImportIsolationError, enforce_isolation, scan_repository
from packages.persistence import assemble_canonical_state
from packages.persistence.assembler import CANONICAL_FIELD_NAMES
from packages.reconciliation import (
    PostActionVerification,
    ReconciliationContext,
    ReconciliationEngine,
    fixture_config,
)

REPO_ROOT = Path(__file__).parents[1]
_OBSERVATION_MODELS: dict[str, Any] = {
    "EXECUTION": ExecutionObservation,
    "TRADE_CAPTURE": TradeCaptureObservation,
    "CONFIRMATION": ConfirmationObservation,
    "BOOKING": BookingObservation,
}
_FAMILIES = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
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


def _build_context(
    raws: list[dict[str, Any]],
    *,
    family: str | None,
    run_id: str,
) -> ReconciliationContext:
    observations = tuple(
        _OBSERVATION_MODELS[raw["observation_kind"]].model_validate(raw) for raw in raws
    )
    baseline = observations[0]
    remaining_fields = list(CANONICAL_FIELD_NAMES)
    selection: dict[str, Any] = {}
    for observation in observations:
        field_name = next(
            (
                candidate
                for candidate in remaining_fields
                if getattr(observation.payload, candidate) == getattr(baseline.payload, candidate)
            ),
            None,
        )
        if field_name is None:
            raise AssertionError(f"no stable canonical field for {observation.observation_id}")
        selection[field_name] = observation
        remaining_fields.remove(field_name)
    for field_name in remaining_fields:
        selection[field_name] = baseline

    trade_id = Counter(item.source_business_key for item in observations).most_common(1)[0][0]
    canonical = assemble_canonical_state(
        trade_id=trade_id,
        canonical_state_version=1,
        field_selection=selection,
        correlation_id=baseline.correlation_id,
        actor=Actor(identity_type="SYSTEM", actor_id="ts12_fixture_assembler"),
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
    return ReconciliationContext(
        reconciliation_run_id=run_id,
        run_version=1,
        canonical_state=canonical,
        source_observations=observations,
        linkage_decision=decision,
    )


def _oracle_inputs(
    context: ReconciliationContext,
) -> dict[str, Any]:
    canonical = context.canonical_state
    provenance = {
        f"/payload/{field_name}": getattr(
            canonical.field_provenance, field_name
        ).source_observation_id
        for field_name in (
            "base_currency",
            "terms_currency",
            "side",
            "base_amount",
            "terms_amount",
            "quoted_rate",
            "trade_date",
            "value_date",
        )
    }
    return {
        "source_observations": [
            item.model_dump(mode="json") for item in context.source_observations
        ],
        "canonical_state": {
            "product_type": canonical.state.product_type,
            "tenant_id": canonical.tenant_id,
            "portfolio_id": canonical.portfolio_id,
            "correlation_id": canonical.correlation_id,
            "trade_id": canonical.trade_id,
            "source_watermark": canonical.source_watermark.isoformat(),
            "source_version_set": [
                {
                    "source_observation_id": item.observation_id,
                    "observation_kind": item.observation_kind,
                    "source_system": item.source_system,
                    "source_version": item.source_version,
                    "content_hash": item.content_hash,
                }
                for item in canonical.source_version_set
            ],
            "field_provenance": provenance,
        },
        "config": fixture_config().model_dump(mode="json"),
        "linkage_decision": (
            None
            if context.linkage_decision is None
            else context.linkage_decision.model_dump(mode="json")
        ),
    }


def _post_action_context(
    corpus: Any,
    *,
    product: str,
    run_id: str,
) -> ReconciliationContext:
    clean = _truth(corpus, product=product, family=None)
    raws = _raws_for_truth(corpus, clean)
    pre_raw = next(raw for raw in raws if raw["observation_kind"] == "BOOKING")
    post_raw = copy.deepcopy(pre_raw)
    post_raw["observation_id"] = f"obs_ts12_post_{product.lower()}"
    post_raw["source_event_id"] = f"evt_ts12_post_{product.lower()}"
    post_raw["source_version"] = "2"
    post_raw["source_sequence"] = 5
    post_raw["ingest_time"] = (
        _OBSERVATION_MODELS["BOOKING"].model_validate(pre_raw).ingest_time + timedelta(minutes=1)
    ).isoformat()
    post_raw["payload"]["book_id"] = f"book_ts12_post_{product.lower()}"
    post_raw["content_hash"] = (
        "sha256:" + hashlib.sha256(f"ts12-post-{product}".encode()).hexdigest()
    )
    raws = [raw for raw in raws if raw["observation_kind"] != "BOOKING"]
    raws.extend([pre_raw, post_raw])
    context = _build_context(raws, family=None, run_id=run_id)
    pre_action = next(
        item
        for item in context.source_observations
        if item.observation_id == pre_raw["observation_id"]
    )
    post_action = next(
        item
        for item in context.source_observations
        if item.observation_id == post_raw["observation_id"]
    )
    assert isinstance(pre_action, BookingObservation)
    assert isinstance(post_action, BookingObservation)
    return ReconciliationContext(
        reconciliation_run_id=context.reconciliation_run_id,
        run_version=context.run_version,
        canonical_state=context.canonical_state,
        source_observations=context.source_observations,
        linkage_decision=context.linkage_decision,
        post_action_verification=PostActionVerification(
            action_instruction_hash="sha256:" + "a" * 64,
            pre_action=pre_action,
            post_action=post_action,
        ),
    )


@pytest.mark.parametrize("product", ["FX_SPOT", "FX_FORWARD"])
@pytest.mark.parametrize("family", [None, *_FAMILIES])
def test_oracle_matches_production_family_outcomes(
    corpus: Any,
    product: str,
    family: str | None,
) -> None:
    if family == "POST_ACTION_VERIFICATION_FAILURE":
        context = _post_action_context(
            corpus, product=product, run_id=f"run_ts12_{product.lower()}"
        )
    else:
        truth = _truth(corpus, product=product, family=family)
        context = _build_context(
            _raws_for_truth(corpus, truth),
            family=family,
            run_id=f"run_ts12_{str(family).lower()}_{product.lower()}",
        )
    inputs = _oracle_inputs(context)
    if context.post_action_verification is not None:
        inputs["post_action_verification"] = context.post_action_verification.model_dump(
            mode="json"
        )

    oracle_result = evaluate(
        source_observations=inputs["source_observations"],
        canonical_state=inputs["canonical_state"],
        config=inputs["config"],
        linkage_decision=inputs["linkage_decision"],
        post_action_verification=inputs.get("post_action_verification"),
    )
    production_result = ReconciliationEngine(fixture_config()).run(context)

    assert set(oracle_result.families) == {item.family for item in production_result.breaks}
    assert (oracle_result.result == "BREAKS_DETECTED") == bool(production_result.breaks)
    if family is None:
        assert oracle_result.families == ()
    else:
        assert family in oracle_result.families


def test_oracle_rejects_non_exact_source_version_set(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    context = _build_context(
        _raws_for_truth(corpus, truth), family=None, run_id="run_ts12_exact_source_001"
    )
    inputs = _oracle_inputs(context)
    inputs["source_observations"][0]["source_version"] = "2"
    with pytest.raises(ValueError, match="source_version_set"):
        evaluate(**inputs)


def test_oracle_rejects_source_after_watermark(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_FORWARD", family=None)
    context = _build_context(
        _raws_for_truth(corpus, truth), family=None, run_id="run_ts12_watermark_001"
    )
    inputs = _oracle_inputs(context)
    inputs["source_observations"][0]["ingest_time"] = (
        context.canonical_state.source_watermark + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValueError, match="after canonical watermark"):
        evaluate(**inputs)


def test_oracle_rejects_production_result_as_input(corpus: Any) -> None:
    truth = _truth(corpus, product="FX_SPOT", family=None)
    context = _build_context(
        _raws_for_truth(corpus, truth), family=None, run_id="run_ts12_no_result_input_001"
    )
    inputs = _oracle_inputs(context)
    inputs["canonical_state"]["result"] = "PASS"
    with pytest.raises(ValueError, match="production evaluator output"):
        evaluate(**inputs)


def test_import_isolation_report_matches_committed_evidence() -> None:
    report = scan_repository(REPO_ROOT).as_dict()
    committed = json.loads(
        (REPO_ROOT / "packages/oracle/evidence/import-isolation.json").read_text()
    )
    assert report == committed
    assert committed["isolated"] is True
    assert committed["direct_forbidden_edges"] == []
    assert committed["oracle_to_reconciliation_paths"] == []
    assert committed["reconciliation_to_oracle_paths"] == []


def test_import_isolation_fails_closed_on_direct_and_transitive_edges(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    for directory in (
        packages,
        packages / "oracle",
        packages / "reconciliation",
        packages / "shared",
    ):
        directory.mkdir()
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (packages / "oracle" / "entry.py").write_text(
        "from packages.reconciliation import engine\n", encoding="utf-8"
    )
    (packages / "reconciliation" / "engine.py").write_text(
        "from packages.shared import bridge\n", encoding="utf-8"
    )
    (packages / "shared" / "bridge.py").write_text(
        "from packages.oracle import entry\n", encoding="utf-8"
    )

    report = scan_repository(tmp_path)
    negative = json.loads(
        (REPO_ROOT / "packages/oracle/evidence/import-isolation-negative.json").read_text()
    )

    assert report.isolated is False
    assert report.direct_forbidden_edges
    assert report.oracle_to_reconciliation_paths
    assert report.reconciliation_to_oracle_paths
    assert report.as_dict() == negative["observed_report"]
    with pytest.raises(ImportIsolationError):
        enforce_isolation(report)


def test_committed_negative_evidence_declares_fail_closed_enforcement() -> None:
    negative = json.loads(
        (REPO_ROOT / "packages/oracle/evidence/import-isolation-negative.json").read_text()
    )
    assert negative["issue"] == 12
    assert negative["adr"] == "ADR-014"
    assert negative["expected"]["isolated"] is False
    assert negative["expected"]["enforcement"] == "ImportIsolationError"
    assert set(negative["expected"]["failure_surfaces"]) == {
        "direct_forbidden_edges",
        "oracle_to_reconciliation_paths",
        "reconciliation_to_oracle_paths",
    }


def test_import_isolation_fails_closed_on_parse_error(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    (packages / "oracle").mkdir(parents=True)
    (packages / "reconciliation").mkdir()
    (packages / "oracle" / "__init__.py").write_text("def broken(:\n", encoding="utf-8")
    (packages / "reconciliation" / "__init__.py").write_text("", encoding="utf-8")

    report = scan_repository(tmp_path)
    assert report.isolated is False
    assert report.parse_errors


def test_committed_parity_matrix_declares_ts12_traceability() -> None:
    matrix = json.loads((REPO_ROOT / "packages/oracle/evidence/parity-matrix.json").read_text())
    assert matrix["issue"] == 12
    assert matrix["adr"] == "ADR-014"
    assert matrix["baseline_main_merge"] == "c2c9a04f27a8665752727c07afdd50c181702e9c"
    assert len(matrix["rows"]) == 18
    assert {row["product_type"] for row in matrix["rows"]} == {"FX_SPOT", "FX_FORWARD"}
    assert {
        row["expected_families"][0] for row in matrix["rows"] if row["expected_families"]
    } == set(_FAMILIES)
