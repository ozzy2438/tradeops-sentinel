"""Product service layer: demo load, reconciliation execution, read models.

This is the only place that wires the existing deterministic core
(generator -> canonical assembly -> reconciliation engine) to the PostgreSQL
adapter. The engine, contracts, hashing, source-of-truth policy and oracle are
consumed as-is and are not modified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import secrets
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, TypedDict

from packages.contracts import compute_observation_content_hash
from packages.contracts.models import (
    Actor,
    BookingObservation,
    CandidateLink,
    CanonicalTradeState,
    ConfirmationObservation,
    ExecutionObservation,
    LinkageDecision,
    SourceOfTruthPolicy,
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
from packages.reconciliation.models import ReconciliationRun

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
_REMEDIATION_ACTOR = Actor(identity_type="SYSTEM", actor_id="remediation_executor")


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


def _reconcile_lineage_group(
    group: list[dict[str, Any]],
    *,
    policy: SourceOfTruthPolicy,
    engine: ReconciliationEngine,
    run_id: str,
    index: int,
    canonical_state_version: int = 1,
) -> tuple[CanonicalTradeState, ReconciliationRun]:
    """Assemble canonical state for one lineage group and reconcile it.

    The single per-trade unit of work shared by ``run_reconciliation`` (every
    lineage group, once, always version 1) and ``rerun_trade_reconciliation``
    (exactly one lineage group, on demand, at whatever version comes next for
    that trade) -- so a scoped post-action rerun exercises the identical
    deterministic path a full product run does, not a parallel
    reimplementation of it.

    The engine compares its policy-authoritative baseline against every other
    observation in the set and flags a break on the first divergent one -- it
    does not resolve "latest per source system" the way the canonical
    assembler does. A remediation correction adds a new BOOKING revision that
    *supersedes* the one it corrects (``supersedes_observation_id``, set by
    ``ingest_corrected_booking_observation``); excluding what has been
    explicitly superseded here means both a scoped post-action rerun and any
    later full-corpus ``run_reconciliation`` pass keep seeing the trade as
    resolved, rather than the fix only holding until the next batch run.
    ``supersedes_observation_id`` carries no other filtering behaviour
    anywhere else in this codebase, so this is a no-op for every one of the
    demo corpus's other lineage groups, none of which ever sets it.
    """

    superseded_ids = {
        raw["supersedes_observation_id"] for raw in group if raw.get("supersedes_observation_id")
    }
    if superseded_ids:
        group = [raw for raw in group if raw["observation_id"] not in superseded_ids]

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
        canonical_state_version=canonical_state_version,
        field_selection=selection,
        source_observations=observations,
        source_of_truth_policy=policy,
        correlation_id=observations[0].correlation_id,
        actor=_ASSEMBLER_ACTOR,
    )
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
    return canonical, engine.run(context)


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
        canonical, result = _reconcile_lineage_group(
            group, policy=policy, engine=engine, run_id=run_id, index=index
        )
        canonical_states.append(canonical)
        trades_by_portfolio[canonical.portfolio_id] += 1
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


def ingest_corrected_booking_observation(
    adapter: PostgresAdapter, *, trade_id: str, field_path: str, approved_value: str
) -> str:
    """Feed an approved legacy-booking correction back in as a new BOOKING
    observation, the same way any other source revision arrives.

    This is what makes a remediation correction actually visible to
    reconciliation: ``MockLegacyBookingAdapter`` only mutates the remediation
    slice's own mock ledger, which the deterministic engine never reads. The
    engine only ever sees ``source_event_inbox`` rows, so the corrected value
    must be re-delivered through the exact same ingestion path every other
    source system uses -- a new observation superseding the prior BOOKING
    revision, source-of-truth-policy-eligible by virtue of its higher
    ``source_version`` (see ``_resolved_observation`` in
    ``packages.persistence.assembler``), not a direct canonical-state patch.

    Returns the new observation_id.
    """

    if not field_path.startswith("/payload/"):
        raise ValueError(f"unsupported field_path {field_path!r}")
    field_name = field_path.removeprefix("/payload/")

    raws = adapter.observation_documents(**SCOPE)
    candidates = [
        raw
        for raw in raws
        if raw["observation_kind"] == "BOOKING" and raw["source_business_key"] == trade_id
    ]
    if not candidates:
        raise ValueError(f"no BOOKING observation found for trade_id={trade_id!r}")
    latest = max(candidates, key=lambda raw: int(raw["source_version"]))

    document = copy.deepcopy(latest)
    now = datetime.now(UTC)
    new_observation_id = f"obs_booking_correction_{secrets.token_hex(12)}"

    document["observation_id"] = new_observation_id
    document["source_event_id"] = f"evt_correction_{secrets.token_hex(12)}"
    document["source_version"] = str(int(latest["source_version"]) + 1)
    document["source_sequence"] = int(latest["source_sequence"]) + 1
    document["event_time"] = now
    document["effective_time"] = now
    document["ingest_time"] = now
    document["actor"] = {
        "identity_type": _REMEDIATION_ACTOR.identity_type,
        "actor_id": _REMEDIATION_ACTOR.actor_id,
    }
    document["supersedes_observation_id"] = latest["observation_id"]
    document["supersession_reason"] = "CORRECTION"

    payload = document["payload"]
    current_amount = payload[field_name]
    if not isinstance(current_amount, dict) or "value" not in current_amount:
        raise ValueError(f"payload field {field_name!r} is not a correctable amount field")
    payload[field_name] = {**current_amount, "value": approved_value}
    payload["booking_version"] = int(payload["booking_version"]) + 1
    payload["last_updated_time"] = now
    fingerprint_source = {
        key: value for key, value in payload.items() if key != "record_fingerprint"
    }
    payload["record_fingerprint"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                fingerprint_source,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
    )
    document["content_hash"] = compute_observation_content_hash(document)

    observation = BookingObservation.model_validate(document)
    adapter.ingest_observations([observation])
    LOGGER.info(
        "remediation_booking_correction_ingested",
        extra={
            "trade_id": trade_id,
            "observation_id": new_observation_id,
            "field_path": field_path,
        },
    )
    return new_observation_id


def rerun_trade_reconciliation(adapter: PostgresAdapter, *, trade_id: str) -> dict[str, Any]:
    """Re-assemble canonical state and re-run reconciliation for one trade.

    Used by the remediation flow's post-action verification (step 10 of the
    controlled-AI remediation slice): after a signed, approved correction has
    been applied and a corrected observation ingested, this confirms the
    specific break is resolved by re-running the exact same deterministic
    pipeline ``run_reconciliation`` uses -- not a parallel or simplified
    reimplementation of it -- scoped to just this trade's lineage group. It
    persists the next canonical_state_version for the trade, but does not
    create a new product-wide ``reconciliation_runs`` history entry; the
    full-batch run history and this scoped verification are deliberately
    separate concerns.
    """

    raws = adapter.observation_documents(**SCOPE)
    raws = raws + adapter.conflict_documents(**SCOPE)
    group = [
        raw
        for raw in raws
        if raw["source_business_key"] == trade_id
        or raw["payload"].get("source_trade_id") == trade_id
    ]
    if not group:
        raise ValueError(f"no observations found for trade_id={trade_id!r}")

    portfolio_id = str(group[0]["portfolio_id"])
    existing = adapter.canonical_state_document(
        tenant_id=SCOPE["tenant_id"], portfolio_ids=(portfolio_id,), trade_id=trade_id
    )
    next_version = int(existing["canonical_state_version"]) + 1 if existing else 1

    policy = load_mvp_source_of_truth_policy()
    config = fixture_config()
    engine = ReconciliationEngine(config)
    evaluated_at = datetime.now(UTC)
    run_stamp = evaluated_at.strftime("%Y%m%d%H%M%S")

    canonical, result = _reconcile_lineage_group(
        group,
        policy=policy,
        engine=engine,
        run_id=f"rerun_{run_stamp}",
        index=0,
        canonical_state_version=next_version,
    )
    adapter.persist_canonical_states([canonical])

    families = sorted({item.family for item in result.breaks})
    LOGGER.info(
        "trade_reconciliation_rerun",
        extra={
            "trade_id": trade_id,
            "result": result.result,
            "break_families": families,
            "canonical_state_version": next_version,
        },
    )
    return {
        "trade_id": trade_id,
        "canonical_state_version": next_version,
        "result": result.result,
        "break_families": families,
        "breaks": [item.model_dump(mode="json") for item in result.breaks],
        "content_hash": canonical.content_hash,
        "evaluated_at": evaluated_at.isoformat(),
    }


def build_summary(adapter: PostgresAdapter) -> dict[str, Any]:
    """Aggregate totals for the dashboard landing view.

    Scoped to the latest *completed* reconciliation run, not to all history.
    reconciliation_runs/trade_breaks are append-only: every run's rows survive
    indefinitely, so summing across the whole scope would silently accumulate
    every past run's breaks into one ever-growing total. The product's default
    view is "what does the latest run say", with full history still reachable
    via GET /runs and GET /breaks?run_id=<historical-run>.
    """

    run_id = adapter.latest_completed_run_id(**SCOPE)
    total_observations = adapter.observation_count(**SCOPE)
    products = adapter.product_counts(**SCOPE)

    if run_id is None:
        # No successful run yet: a controlled empty result, not an error.
        return {
            "tenant_id": DEMO_TENANT_ID,
            "portfolio_ids": list(DEMO_PORTFOLIO_IDS),
            "total_observations": total_observations,
            "total_trades": 0,
            "broken_trades": 0,
            "clean_trades": 0,
            "total_breaks": 0,
            "breaks_by_family": {},
            "trades_by_product": products,
            "latest_run_id": None,
            "latest_run_completed_at": None,
            "config_hash": None,
        }

    # One reconciliation invocation persists one reconciliation_runs row per
    # portfolio under the same run_id; summing their own stored counters is
    # the authoritative per-run total, not re-derived via DISTINCT queries
    # that would coincidentally look right only because reruns are idempotent.
    portfolio_runs = adapter.runs_by_run_id(**SCOPE, run_id=run_id)
    families = adapter.break_family_counts(**SCOPE, run_id=run_id)
    return {
        "tenant_id": DEMO_TENANT_ID,
        "portfolio_ids": list(DEMO_PORTFOLIO_IDS),
        "total_observations": total_observations,
        "total_trades": sum(row["trades_evaluated"] for row in portfolio_runs),
        "broken_trades": sum(row["broken_trades"] for row in portfolio_runs),
        "clean_trades": sum(row["clean_trades"] for row in portfolio_runs),
        "total_breaks": sum(row["break_count"] for row in portfolio_runs),
        "breaks_by_family": families,
        "trades_by_product": products,
        "latest_run_id": run_id,
        "latest_run_completed_at": max(row["completed_at"] for row in portfolio_runs),
        "config_hash": str(portfolio_runs[0]["config_hash"]),
    }
