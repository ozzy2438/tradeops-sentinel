"""Product service layer: demo load, reconciliation execution, read models.

This is the only place that wires the existing deterministic core
(generator -> canonical assembly -> reconciliation engine) to the PostgreSQL
adapter. The engine, contracts, hashing, source-of-truth policy and oracle are
consumed as-is and are not modified.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, TypedDict

from packages.contracts.models import (
    Actor,
    BookingObservation,
    CandidateLink,
    ConfirmationObservation,
    ExecutionObservation,
    LinkageDecision,
    TradeBreak,
    TradeCaptureObservation,
)
from packages.generator.core import generate_corpus
from packages.persistence import (
    assemble_canonical_state,
    load_mvp_source_of_truth_policy,
    resolve_field_selection,
)
from packages.persistence.adapter import IngestSummary, PostgresAdapter
from packages.reconciliation import ReconciliationContext, ReconciliationEngine
from packages.reconciliation.config import fixture_config

LOGGER = logging.getLogger("tradeops.api.service")

# The reconciliation context requires the concrete observation union, not the
# base envelope, so the registry is typed to that union.
SourceObservation = (
    ExecutionObservation | TradeCaptureObservation | ConfirmationObservation | BookingObservation
)

OBSERVATION_MODELS: dict[str, type[SourceObservation]] = {
    "EXECUTION": ExecutionObservation,
    "TRADE_CAPTURE": TradeCaptureObservation,
    "CONFIRMATION": ConfirmationObservation,
    "BOOKING": BookingObservation,
}

# The demo corpus is single-tenant synthetic data spanning two portfolios.
# Every read path is scoped to this tenant and this explicit portfolio
# allowlist, so no query can return data outside the demo scope.
DEMO_TENANT_ID = "tenant_demo"
DEMO_PORTFOLIO_IDS: tuple[str, ...] = ("portfolio_london", "portfolio_sydney")


class Scope(TypedDict):
    """Typed read scope so ``**SCOPE`` keeps its types through mypy --strict."""

    tenant_id: str
    portfolio_ids: tuple[str, ...]


SCOPE: Scope = {"tenant_id": DEMO_TENANT_ID, "portfolio_ids": DEMO_PORTFOLIO_IDS}

_ASSEMBLER_ACTOR = Actor(identity_type="SYSTEM", actor_id="canonical_assembler")
_LINKAGE_ACTOR = Actor(identity_type="SYSTEM", actor_id="linkage_engine")


class DemoScopeError(RuntimeError):
    """Raised when generated data falls outside the single demo scope."""


def load_demo_corpus(adapter: PostgresAdapter) -> IngestSummary:
    """Ingest the existing approved synthetic FX corpus.

    Re-running this is safe: identical observations are recognised as
    idempotent replays by the adapter and produce no new rows.
    """

    corpus = generate_corpus()
    observations: list[SourceObservation] = []
    for raw in corpus.source_observations:
        if raw["tenant_id"] != DEMO_TENANT_ID or raw["portfolio_id"] not in DEMO_PORTFOLIO_IDS:
            raise DemoScopeError("generated corpus escaped the demo tenant/portfolio scope")
        model = OBSERVATION_MODELS[raw["observation_kind"]]
        observations.append(model.model_validate(raw))
    summary = adapter.ingest_observations(observations, on_conflict="quarantine")
    LOGGER.info(
        "demo_corpus_loaded",
        extra={"inserted": summary.inserted, "replayed": summary.replayed},
    )
    return summary


def _linkage_decision(
    *,
    run_id: str,
    trade_id: str,
    canonical: Any,
    observations: tuple[SourceObservation, ...],
) -> LinkageDecision:
    """Derive the linkage decision from the observation data itself.

    A dedicated linkage engine is out of scope for this MVP, so the decision is
    computed deterministically from what the sources actually say. It is
    derived from observation content only -- never from the generator's
    evaluator-only truth ledger, which must not influence detection.
    """

    base = dict(
        decision_id=f"decision_{run_id}",
        entity_version=1,
        tenant_id=canonical.tenant_id,
        portfolio_id=canonical.portfolio_id,
        correlation_id=canonical.correlation_id,
        content_hash="sha256:" + "1" * 64,
        created_at=canonical.source_watermark,
        actor=_LINKAGE_ACTOR,
        source_observation_id=observations[0].observation_id,
        deterministic_rule_version="1.0.0",
    )

    distinct_payload_trades = {item.payload.source_trade_id for item in observations}
    has_foreign_business_key = any(item.source_business_key != trade_id for item in observations)

    # More observations than the four canonical kinds means an extra candidate
    # was delivered; combined with a disagreeing trade identity that is an
    # ambiguous linkage rather than a plain unmatched one.
    if len(observations) > 4 and len(distinct_payload_trades) > 1:
        return LinkageDecision(
            **base,
            decision="AMBIGUOUS",
            candidate_links=[
                CandidateLink(
                    trade_id=candidate,
                    tenant_id=canonical.tenant_id,
                    portfolio_id=canonical.portfolio_id,
                    match_key=f"match_key_{index:03d}",
                    match_rule_version="1.0.0",
                    evidence_hash="sha256:" + str(index + 2) * 64,
                )
                for index, candidate in enumerate(sorted(distinct_payload_trades))
            ],
            chosen_trade_id=None,
            reason_code="MULTIPLE_ELIGIBLE_CANDIDATES",
        )

    if has_foreign_business_key:
        return LinkageDecision(
            **base,
            decision="UNMATCHED",
            candidate_links=[],
            chosen_trade_id=None,
            reason_code="NO_ELIGIBLE_CANDIDATE",
        )

    return LinkageDecision(
        **base,
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


def run_reconciliation(adapter: PostgresAdapter) -> dict[str, Any]:
    """Assemble canonical state per lineage group and reconcile every trade."""

    started_at = datetime.now(UTC)
    raws = adapter.observation_documents(**SCOPE)
    # Quarantined conflicting deliveries are part of what the sources actually
    # sent, so reconciliation must see them to raise DUPLICATE_SOURCE_CONFLICT.
    raws = raws + adapter.conflict_documents(**SCOPE)
    if not raws:
        raise ValueError("no observations ingested; load demo data first")

    by_lineage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in raws:
        by_lineage[str(raw["lineage_group_id"])].append(raw)

    policy = load_mvp_source_of_truth_policy()
    config = fixture_config()
    engine = ReconciliationEngine(config)
    run_stamp = started_at.strftime("%Y%m%d%H%M%S")
    run_id = f"run_{run_stamp}"

    canonical_states = []
    # Runs, breaks and counters are tracked per portfolio: a reconciliation run
    # row is portfolio-scoped, so one logical run writes one row per portfolio
    # rather than blurring two portfolios into a single aggregate.
    breaks_by_portfolio: dict[str, list[TradeBreak]] = defaultdict(list)
    clean_by_portfolio: Counter[str] = Counter()
    broken_by_portfolio: Counter[str] = Counter()
    trades_by_portfolio: Counter[str] = Counter()
    observations_by_portfolio: Counter[str] = Counter()
    for raw in raws:
        observations_by_portfolio[str(raw["portfolio_id"])] += 1

    for index, (lineage_id, group) in enumerate(sorted(by_lineage.items())):
        observations = tuple(
            OBSERVATION_MODELS[str(raw["observation_kind"])].model_validate(raw) for raw in group
        )
        trade_id = Counter(item.source_business_key for item in observations).most_common(1)[0][0]
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
            correlation_id=observations[0].correlation_id,
            actor=_ASSEMBLER_ACTOR,
        )
        canonical_states.append(canonical)
        trades_by_portfolio[canonical.portfolio_id] += 1

        context = ReconciliationContext(
            reconciliation_run_id=f"{run_id}_{index:04d}",
            run_version=1,
            canonical_state=canonical,
            source_observations=observations,
            linkage_decision=_linkage_decision(
                run_id=f"{run_id}_{index:04d}",
                trade_id=trade_id,
                canonical=canonical,
                observations=observations,
            ),
        )
        result = engine.run(context)
        if result.breaks:
            broken_by_portfolio[canonical.portfolio_id] += 1
            breaks_by_portfolio[canonical.portfolio_id].extend(result.breaks)
        else:
            clean_by_portfolio[canonical.portfolio_id] += 1
        LOGGER.debug("trade_reconciled", extra={"lineage": lineage_id, "result": result.result})

    adapter.persist_canonical_states(canonical_states)
    for portfolio_id in DEMO_PORTFOLIO_IDS:
        adapter.persist_run(
            run_id=run_id,
            run_version=1,
            tenant_id=DEMO_TENANT_ID,
            portfolio_id=portfolio_id,
            config_id=config.config_id,
            config_version=config.config_version,
            config_hash=config.content_hash,
            detection_rule_version=config.detection_rule_version,
            trades_evaluated=trades_by_portfolio[portfolio_id],
            observations_ingested=observations_by_portfolio[portfolio_id],
            clean_trades=clean_by_portfolio[portfolio_id],
            broken_trades=broken_by_portfolio[portfolio_id],
            breaks=breaks_by_portfolio[portfolio_id],
            status="COMPLETED",
            started_at=started_at,
        )
    all_breaks = [item for items in breaks_by_portfolio.values() for item in items]
    clean_trades = sum(clean_by_portfolio.values())
    broken_trades = sum(broken_by_portfolio.values())
    LOGGER.info(
        "reconciliation_completed",
        extra={
            "run_id": run_id,
            "trades": len(canonical_states),
            "breaks": len(all_breaks),
        },
    )
    return {
        "run_id": run_id,
        "trades_evaluated": len(canonical_states),
        "clean_trades": clean_trades,
        "broken_trades": broken_trades,
        "break_count": len(all_breaks),
        "config_hash": config.content_hash,
    }


def build_summary(adapter: PostgresAdapter) -> dict[str, Any]:
    """Aggregate totals for the dashboard landing view."""

    trades = adapter.canonical_trade_count(**SCOPE)
    broken = adapter.broken_trade_ids(**SCOPE)
    families = adapter.break_family_counts(**SCOPE)
    latest = adapter.latest_run(**SCOPE)
    return {
        "tenant_id": DEMO_TENANT_ID,
        "portfolio_ids": list(DEMO_PORTFOLIO_IDS),
        "total_observations": adapter.observation_count(**SCOPE),
        "total_trades": trades,
        "broken_trades": len(broken),
        "clean_trades": max(trades - len(broken), 0),
        "total_breaks": sum(families.values()),
        "breaks_by_family": families,
        "trades_by_product": adapter.product_counts(**SCOPE),
        "latest_run_id": str(latest["run_id"]) if latest else None,
        "latest_run_completed_at": latest["completed_at"] if latest else None,
        "config_hash": str(latest["config_hash"]) if latest else None,
    }
