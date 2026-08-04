"""TradeOps Sentinel product API.

A deliberately small FastAPI surface over the existing deterministic core.
Nine product endpoints plus five controlled-AI remediation endpoints (see
``docs/AI_REMEDIATION.md``), one API-key guard, typed errors. No
registration, OAuth, JWT or RBAC -- those are explicitly out of scope for
this MVP.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.persistence.adapter import DatabaseUnavailableError, PostgresAdapter
from packages.persistence.inbox import SourceConflictError
from packages.remediation import triage as remediation_triage
from packages.remediation.ai_provider import AIProvider, get_provider
from packages.remediation.envelope import EnvelopeSigningError, build_envelope
from packages.remediation.evidence import case_view, finalize_evidence
from packages.remediation.executor import RemediationExecutor
from packages.remediation.legacy_adapter import MockLegacyBookingAdapter
from packages.remediation.models import (
    ActionEnvelope,
    AIRecommendation,
    Approval,
    PolicyDecision,
    recommendation_content_hash,
)
from packages.remediation.store import DuplicateApprovalRoleError, RemediationStore

from .service import (
    DEMO_PORTFOLIO_IDS,
    DEMO_TENANT_ID,
    SCOPE,
    DemoScopeError,
    build_summary,
    ingest_corrected_booking_observation,
    load_demo_corpus,
    rerun_trade_reconciliation,
    run_reconciliation,
)


class _JsonLogFormatter(logging.Formatter):
    """Structured single-line JSON logs, safe for container log scraping."""

    _NOISE = {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "time": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
        }
        for key, value in record.__dict__.items():
            if key not in self._NOISE and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


LOGGER = logging.getLogger("tradeops.api")

# --------------------------------------------------------------------------
# settings / dependencies
# --------------------------------------------------------------------------


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise DatabaseUnavailableError("DATABASE_URL is not configured")
    return url


def get_adapter() -> PostgresAdapter:
    return PostgresAdapter(_database_url())


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Constant-shape API-key guard.

    A missing server-side key is a misconfiguration, not an open door: the
    service refuses every authenticated request rather than defaulting open.
    """

    expected = os.getenv("TRADEOPS_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service is not configured for authenticated access",
        )
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )


Guard = Annotated[None, Depends(require_api_key)]
Adapter = Annotated[PostgresAdapter, Depends(get_adapter)]


def get_remediation_store(adapter: Adapter) -> RemediationStore:
    return RemediationStore(adapter)


RemediationStoreDep = Annotated[RemediationStore, Depends(get_remediation_store)]


def get_legacy_adapter(store: RemediationStoreDep) -> MockLegacyBookingAdapter:
    return MockLegacyBookingAdapter(store)


LegacyAdapterDep = Annotated[MockLegacyBookingAdapter, Depends(get_legacy_adapter)]


def get_executor(
    store: RemediationStoreDep, legacy_adapter: LegacyAdapterDep
) -> RemediationExecutor:
    return RemediationExecutor(store, legacy_adapter)


ExecutorDep = Annotated[RemediationExecutor, Depends(get_executor)]


def get_ai_provider() -> AIProvider:
    return get_provider()


ProviderDep = Annotated[AIProvider, Depends(get_ai_provider)]


# --------------------------------------------------------------------------
# response models
# --------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "tradeops-sentinel-api"


class ReadyResponse(BaseModel):
    ready: bool
    detail: str


class DemoLoadResponse(BaseModel):
    inserted: int
    replayed: int
    conflicted: int
    total: int
    tenant_id: str
    portfolio_ids: list[str]


class RunResponse(BaseModel):
    run_id: str
    trades_evaluated: int
    clean_trades: int
    broken_trades: int
    break_count: int
    config_hash: str


class SummaryResponse(BaseModel):
    tenant_id: str
    portfolio_ids: list[str]
    total_observations: int
    total_trades: int
    clean_trades: int
    broken_trades: int
    total_breaks: int
    breaks_by_family: dict[str, int]
    trades_by_product: dict[str, int]
    latest_run_id: str | None = None
    latest_run_completed_at: datetime | None = None
    config_hash: str | None = None


class BreakRow(BaseModel):
    break_id: str
    break_version: int
    run_id: str
    trade_id: str
    product_type: str
    break_family: str
    condition_code: str
    severity: str
    state: str
    detected_at: datetime
    canonical_state_version: int
    config_hash: str


class BreakDetail(BreakRow):
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_version_set: list[dict[str, Any]] = Field(default_factory=list)
    evaluated_field_paths: list[str] = Field(default_factory=list)
    detection_rule_version: str | None = None
    taxonomy_version: str | None = None
    priority: dict[str, Any] | None = None
    break_document: dict[str, Any] = Field(default_factory=dict)


class TradeDetail(BaseModel):
    trade_id: str
    canonical_state_version: int
    tenant_id: str
    portfolio_id: str
    correlation_id: str
    content_hash: str
    as_of_time: datetime
    source_watermark: datetime
    state: dict[str, Any]
    field_provenance: dict[str, Any]
    source_version_set: list[dict[str, Any]]
    breaks: list[BreakRow]


class RemediationCaseRequest(BaseModel):
    break_id: str = Field(min_length=1)


class RemediationCaseResponse(BaseModel):
    case_id: str
    recommendation: AIRecommendation
    policy_decision: PolicyDecision


class RemediationApprovalRequest(BaseModel):
    approver_identity: str = Field(min_length=1, max_length=200)


class ErrorResponse(BaseModel):
    error: str
    detail: str


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    LOGGER.info("api_starting")
    if os.getenv("TRADEOPS_AUTO_MIGRATE", "true").lower() == "true":
        try:
            applied = PostgresAdapter(_database_url()).migrate()
            LOGGER.info("startup_migrations_applied", extra={"count": len(applied)})
        except Exception as error:  # noqa: BLE001 - startup must not crash-loop
            LOGGER.warning("startup_migration_skipped", extra={"reason": type(error).__name__})
    yield
    LOGGER.info("api_stopping")


app = FastAPI(
    title="TradeOps Sentinel",
    version="0.1.0",
    summary="Deterministic FX Spot/Forward trade-break reconciliation (synthetic data only).",
    description=(
        "Loads an approved synthetic FX corpus, assembles policy-enforced canonical "
        "trade state, runs the deterministic reconciliation engine, and exposes the "
        "detected breaks with full provenance.\n\n"
        "**Synthetic demonstration data only — no real banking or customer data.**"
    ),
    lifespan=lifespan,
)


def _safe_error(exc: Exception, fallback: str) -> ErrorResponse:
    """Never leak stack traces, DSNs or driver internals to a client."""

    return ErrorResponse(error=type(exc).__name__, detail=fallback)


@app.exception_handler(DatabaseUnavailableError)
async def _db_unavailable(_: Any, exc: DatabaseUnavailableError) -> JSONResponse:
    LOGGER.error("database_unavailable", extra={"error": type(exc).__name__})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_safe_error(exc, "database is unavailable").model_dump(),
    )


@app.exception_handler(SourceConflictError)
async def _source_conflict(_: Any, exc: SourceConflictError) -> JSONResponse:
    LOGGER.warning("duplicate_source_conflict")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=ErrorResponse(
            error="DUPLICATE_SOURCE_CONFLICT",
            detail=(
                "same source identity/version arrived with different verified content; "
                "ingestion was rolled back"
            ),
        ).model_dump(),
    )


@app.exception_handler(EnvelopeSigningError)
async def _envelope_signing_unavailable(_: Any, exc: EnvelopeSigningError) -> JSONResponse:
    LOGGER.error("remediation_signing_secret_unavailable", extra={"error": type(exc).__name__})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_safe_error(exc, "remediation signing secret is not configured").model_dump(),
    )


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness only -- deliberately does not touch the database."""

    return HealthResponse()


@app.get("/ready", response_model=ReadyResponse, tags=["ops"])
def ready(adapter: Adapter) -> JSONResponse:
    """Readiness: database reachable *and* schema migrated."""

    try:
        ok, detail = adapter.is_ready()
    except DatabaseUnavailableError:
        ok, detail = False, "database unreachable"
    payload = ReadyResponse(ready=ok, detail=detail)
    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )


@app.post("/demo/load", response_model=DemoLoadResponse, tags=["demo"])
def demo_load(adapter: Adapter, _: Guard) -> DemoLoadResponse:
    """Load the approved synthetic FX corpus. Safe to call repeatedly."""

    adapter.migrate()
    try:
        summary = load_demo_corpus(adapter)
    except DemoScopeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return DemoLoadResponse(
        inserted=summary.inserted,
        replayed=summary.replayed,
        conflicted=summary.conflicted,
        total=summary.total,
        tenant_id=DEMO_TENANT_ID,
        portfolio_ids=list(DEMO_PORTFOLIO_IDS),
    )


@app.post("/reconciliation/run", response_model=RunResponse, tags=["reconciliation"])
def reconciliation_run(adapter: Adapter, _: Guard) -> RunResponse:
    """Execute the existing deterministic reconciliation engine."""

    try:
        result = run_reconciliation(adapter)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RunResponse(**result)


@app.get("/summary", response_model=SummaryResponse, tags=["reporting"])
def summary(adapter: Adapter, _: Guard) -> SummaryResponse:
    return SummaryResponse(**build_summary(adapter))


@app.get("/runs", tags=["reporting"])
def runs(adapter: Adapter, _: Guard, limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    return adapter.recent_runs(**SCOPE, limit=limit)


@app.get("/breaks", response_model=list[BreakRow], tags=["reporting"])
def breaks(
    adapter: Adapter,
    _: Guard,
    product_type: str | None = Query(None, pattern="^(FX_SPOT|FX_FORWARD)$"),
    break_family: str | None = None,
    state: str | None = None,
    run_id: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
) -> list[BreakRow]:
    """Breaks for one reconciliation run.

    Without an explicit ``run_id`` this defaults to the latest completed run.
    trade_breaks is append-only, so an unscoped query would silently include
    every historical run's rows. Pass ``run_id`` explicitly to inspect a
    specific historical run -- see GET /runs for the available ids.
    """

    effective_run_id = run_id or adapter.latest_completed_run_id(**SCOPE)
    if effective_run_id is None:
        return []
    rows = adapter.query_breaks(
        **SCOPE,
        product_type=product_type,
        break_family=break_family,
        state=state,
        run_id=effective_run_id,
        limit=limit,
    )
    return [BreakRow(**row) for row in rows]


@app.get("/breaks/{break_id}", response_model=BreakDetail, tags=["reporting"])
def break_detail(adapter: Adapter, _: Guard, break_id: str) -> BreakDetail:
    row = adapter.break_detail(**SCOPE, break_id=break_id)
    if row is None:
        raise HTTPException(status_code=404, detail="break not found")
    document = row.pop("break_document")
    source_version_set = row.pop("source_version_set")
    return BreakDetail(
        **row,
        comparisons=document.get("comparisons", []) or [],
        evidence=document.get("evidence", []) or [],
        source_version_set=source_version_set or [],
        evaluated_field_paths=document.get("evaluated_field_paths", []) or [],
        detection_rule_version=document.get("detection_rule_version"),
        taxonomy_version=document.get("taxonomy_version"),
        priority=document.get("priority"),
        break_document=document,
    )


@app.get("/trades/{trade_id}", response_model=TradeDetail, tags=["reporting"])
def trade_detail(adapter: Adapter, _: Guard, trade_id: str) -> TradeDetail:
    row = adapter.canonical_state_document(**SCOPE, trade_id=trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="trade not found")
    # Related breaks are scoped to the latest run for the same reason as the
    # default /breaks view: without it, a trade broken in every historical
    # run would show one duplicate entry per run instead of its current state.
    latest_run_id = adapter.latest_completed_run_id(**SCOPE)
    related = (
        adapter.query_breaks(**SCOPE, run_id=latest_run_id, limit=2000)
        if latest_run_id is not None
        else []
    )
    return TradeDetail(
        trade_id=str(row["trade_id"]),
        canonical_state_version=int(row["canonical_state_version"]),
        tenant_id=str(row["tenant_id"]),
        portfolio_id=str(row["portfolio_id"]),
        correlation_id=str(row["correlation_id"]),
        content_hash=str(row["content_hash"]),
        as_of_time=row["as_of_time"],
        source_watermark=row["source_watermark"],
        state=row["state"],
        field_provenance=row["field_provenance"],
        source_version_set=row["source_version_set"],
        breaks=[BreakRow(**item) for item in related if item["trade_id"] == trade_id],
    )


# --------------------------------------------------------------------------
# remediation (controlled-AI remediation slice)
# --------------------------------------------------------------------------


@app.post(
    "/remediation/cases",
    response_model=RemediationCaseResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["remediation"],
)
def remediation_create_case(
    adapter: Adapter,
    store: RemediationStoreDep,
    provider: ProviderDep,
    _: Guard,
    body: RemediationCaseRequest,
) -> RemediationCaseResponse:
    """Detect -> AI triage -> deterministic policy evaluation for one break.

    Scoped to the single ECONOMIC_VALUE_MISMATCH/base_amount scenario this
    slice supports; anything else is rejected, not silently generalised.
    """

    try:
        result = remediation_triage.generate_case(
            break_id=body.break_id,
            scope=dict(SCOPE),
            product_adapter=adapter,
            store=store,
            provider=provider,
        )
    except remediation_triage.BreakNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except remediation_triage.CaseNotEligibleError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RemediationCaseResponse(
        case_id=result["case_id"],
        recommendation=result["recommendation"],
        policy_decision=result["decision"],
    )


def _require_remediation_case(store: RemediationStore, case_id: str) -> dict[str, Any]:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="remediation case not found")
    return case


def _submit_remediation_approval(
    store: RemediationStore, *, case_id: str, role: str, approver_identity: str
) -> dict[str, Any]:
    case = _require_remediation_case(store, case_id)
    decision = PolicyDecision.model_validate(case["policy_decision"])
    if decision.outcome != "ELIGIBLE_FOR_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"case is not eligible for approval (outcome={decision.outcome})",
        )
    existing = store.get_approvals(case_id)
    other_role_identity = next(
        (row["approver_identity"] for row in existing if row["role"] != role), None
    )
    if other_role_identity is not None and other_role_identity == approver_identity:
        raise HTTPException(
            status_code=409, detail="maker and checker must be different identities"
        )
    rec_hash = recommendation_content_hash(case["ai_recommendation"])
    try:
        store.insert_approval(
            case_id=case_id,
            role=role,
            approver_identity=approver_identity,
            decision="APPROVE",
            approved_recommendation_hash=rec_hash,
        )
    except DuplicateApprovalRoleError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    view = case_view(store, case_id)
    assert view is not None
    return view


@app.post("/remediation/cases/{case_id}/maker-approval", tags=["remediation"])
def remediation_maker_approval(
    store: RemediationStoreDep, _: Guard, case_id: str, body: RemediationApprovalRequest
) -> dict[str, Any]:
    """Maker approval for the proposed economic-field correction."""

    return _submit_remediation_approval(
        store, case_id=case_id, role="MAKER", approver_identity=body.approver_identity
    )


@app.post("/remediation/cases/{case_id}/checker-approval", tags=["remediation"])
def remediation_checker_approval(
    store: RemediationStoreDep, _: Guard, case_id: str, body: RemediationApprovalRequest
) -> dict[str, Any]:
    """Checker approval for the proposed economic-field correction."""

    return _submit_remediation_approval(
        store, case_id=case_id, role="CHECKER", approver_identity=body.approver_identity
    )


@app.post("/remediation/cases/{case_id}/execute", tags=["remediation"])
def remediation_execute(
    adapter: Adapter,
    store: RemediationStoreDep,
    executor: ExecutorDep,
    _: Guard,
    case_id: str,
) -> dict[str, Any]:
    """Execute the signed, approved action; on success, verify and finalise evidence.

    Safe to call more than once for the same case: the envelope is issued
    once and reused (its idempotency_key never changes), and re-executing an
    already-applied correction reads back as a confirmed no-op rather than
    repeating the write or re-running post-action verification.
    """

    case = _require_remediation_case(store, case_id)
    decision = PolicyDecision.model_validate(case["policy_decision"])
    if decision.outcome != "ELIGIBLE_FOR_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"case is not eligible for execution (outcome={decision.outcome})",
        )
    approvals_rows = store.get_approvals(case_id)
    maker_row = next((row for row in approvals_rows if row["role"] == "MAKER"), None)
    checker_row = next((row for row in approvals_rows if row["role"] == "CHECKER"), None)
    if maker_row is None or checker_row is None:
        raise HTTPException(
            status_code=409,
            detail="both maker and checker approval are required before execution",
        )

    envelope_row = store.get_envelope(case_id)
    if envelope_row is None:
        assert decision.approved_field_path is not None
        assert decision.approved_value is not None
        draft = build_envelope(
            case_id=case_id,
            trade_id=case["trade_id"],
            tenant_id=case["tenant_id"],
            portfolio_id=case["portfolio_id"],
            field_path=decision.approved_field_path,
            approved_value=decision.approved_value,
            expected_old_value=case["break_facts"]["observed_value"],
            maker_identity=maker_row["approver_identity"],
            checker_identity=checker_row["approver_identity"],
            idempotency_key=f"idem_{case_id}",
        )
        envelope_row = store.insert_envelope_if_absent(
            case_id=case_id,
            idempotency_key=draft.idempotency_key,
            envelope_document=draft.model_dump(mode="json"),
            content_hash=draft.content_hash,
            issued_at=draft.issued_at,
            expires_at=draft.expires_at,
        )
    envelope = ActionEnvelope.model_validate(envelope_row["envelope_document"])

    approvals = [
        Approval(
            role=row["role"],
            approver_identity=row["approver_identity"],
            decision=row["decision"],
            approved_recommendation_hash=row["approved_recommendation_hash"],
            decided_at=row["decided_at"],
        )
        for row in approvals_rows
    ]
    result = executor.execute(envelope, approvals)

    post_action_reconciliation: dict[str, Any] | None = None
    if result.applied:
        ingest_corrected_booking_observation(
            adapter,
            trade_id=case["trade_id"],
            field_path=envelope.field_path,
            approved_value=envelope.approved_value,
        )
        post_action_reconciliation = rerun_trade_reconciliation(adapter, trade_id=case["trade_id"])
        finalize_evidence(
            store, case_id=case_id, post_action_reconciliation=post_action_reconciliation
        )

    view = case_view(store, case_id)
    assert view is not None
    return {"execution_result": result.model_dump(mode="json"), "case": view}


@app.get("/remediation/cases/{case_id}/evidence", tags=["remediation"])
def remediation_evidence(store: RemediationStoreDep, _: Guard, case_id: str) -> dict[str, Any]:
    """Full machine-readable evidence for one remediation case.

    Available at any stage: before a terminal outcome this reflects the
    case's current state; once execution succeeds and post-action
    verification confirms the break resolved, it also carries the one
    frozen evidence snapshot.
    """

    view = case_view(store, case_id)
    if view is None:
        raise HTTPException(status_code=404, detail="remediation case not found")
    return view
