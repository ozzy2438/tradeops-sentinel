"""TradeOps Sentinel product API.

A deliberately small FastAPI surface over the existing deterministic core.
Product, controlled-AI remediation and attended-UiPath browser endpoints
(see ``docs/AI_REMEDIATION.md``), one API-key guard for operator endpoints,
typed errors. No registration, OAuth, JWT or RBAC -- those are explicitly
out of scope for this MVP.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import escape
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from packages.persistence.adapter import DatabaseUnavailableError, PostgresAdapter
from packages.persistence.inbox import SourceConflictError
from packages.priority_model.models import (
    PriorityAssessment,
    PriorityModelUnavailableError,
    PriorityProvider,
)
from packages.priority_model.provider import LightGBMPriorityProvider
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
from packages.remediation.uipath import issue_launch_credential, token_matches

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


@lru_cache(maxsize=1)
def get_priority_provider() -> PriorityProvider:
    """Load the validated immutable model tuple once per API process."""

    return LightGBMPriorityProvider()


PriorityProviderDep = Annotated[PriorityProvider, Depends(get_priority_provider)]


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
    priority_assessment: PriorityAssessment
    policy_decision: PolicyDecision


class RemediationApprovalRequest(BaseModel):
    approver_identity: str = Field(min_length=1, max_length=200)


class UiPathPrepareResponse(BaseModel):
    run_id: str
    case_id: str
    project_name: str
    execution_mode: Literal["ATTENDED_COMMUNITY"] = "ATTENDED_COMMUNITY"
    launch_url: str
    expires_at: datetime


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


@app.exception_handler(PriorityModelUnavailableError)
async def _priority_model_unavailable(_: Any, exc: PriorityModelUnavailableError) -> JSONResponse:
    """Fail closed and never fabricate a queue priority."""

    LOGGER.error("priority_model_unavailable", extra={"error": type(exc).__name__})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_safe_error(exc, "priority model is unavailable").model_dump(),
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
    priority_provider: PriorityProviderDep,
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
            priority_provider=priority_provider,
        )
    except remediation_triage.BreakNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except remediation_triage.CaseNotEligibleError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RemediationCaseResponse(
        case_id=result["case_id"],
        recommendation=result["recommendation"],
        priority_assessment=result["priority_assessment"],
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


UIPATH_PROJECT_NAME = "TradeOps Sentinel Attended Executor"


def _load_approvals(
    store: RemediationStore, case_id: str
) -> tuple[list[Approval], dict[str, Any], dict[str, Any]]:
    rows = store.get_approvals(case_id)
    maker = next((row for row in rows if row["role"] == "MAKER"), None)
    checker = next((row for row in rows if row["role"] == "CHECKER"), None)
    if maker is None or checker is None:
        raise HTTPException(
            status_code=409,
            detail="both maker and checker approval are required before execution",
        )
    approvals = [
        Approval(
            role=row["role"],
            approver_identity=row["approver_identity"],
            decision=row["decision"],
            approved_recommendation_hash=row["approved_recommendation_hash"],
            decided_at=row["decided_at"],
        )
        for row in rows
    ]
    return approvals, maker, checker


def _load_or_issue_envelope(
    store: RemediationStore,
    *,
    case: dict[str, Any],
    decision: PolicyDecision,
    maker: dict[str, Any],
    checker: dict[str, Any],
) -> ActionEnvelope:
    envelope_row = store.get_envelope(case["case_id"])
    if envelope_row is None:
        assert decision.approved_field_path is not None
        assert decision.approved_value is not None
        draft = build_envelope(
            case_id=case["case_id"],
            trade_id=case["trade_id"],
            tenant_id=case["tenant_id"],
            portfolio_id=case["portfolio_id"],
            field_path=decision.approved_field_path,
            approved_value=decision.approved_value,
            expected_old_value=case["break_facts"]["observed_value"],
            maker_identity=maker["approver_identity"],
            checker_identity=checker["approver_identity"],
            idempotency_key=f"idem_{case['case_id']}",
        )
        envelope_row = store.insert_envelope_if_absent(
            case_id=case["case_id"],
            idempotency_key=draft.idempotency_key,
            envelope_document=draft.model_dump(mode="json"),
            content_hash=draft.content_hash,
            issued_at=draft.issued_at,
            expires_at=draft.expires_at,
        )
    return ActionEnvelope.model_validate(envelope_row["envelope_document"])


def _verify_after_applied_action(
    adapter: PostgresAdapter,
    store: RemediationStore,
    *,
    case: dict[str, Any],
    envelope: ActionEnvelope,
) -> dict[str, Any]:
    ingest_corrected_booking_observation(
        adapter,
        trade_id=case["trade_id"],
        field_path=envelope.field_path,
        approved_value=envelope.approved_value,
    )
    post_action = rerun_trade_reconciliation(adapter, trade_id=case["trade_id"])
    finalize_evidence(store, case_id=case["case_id"], post_action_reconciliation=post_action)
    return post_action


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
    approvals, maker, checker = _load_approvals(store, case_id)
    envelope = _load_or_issue_envelope(
        store, case=case, decision=decision, maker=maker, checker=checker
    )
    result = executor.execute(envelope, approvals)

    if result.applied:
        _verify_after_applied_action(adapter, store, case=case, envelope=envelope)

    view = case_view(store, case_id)
    assert view is not None
    return {"execution_result": result.model_dump(mode="json"), "case": view}


@app.post(
    "/remediation/cases/{case_id}/uipath/prepare",
    response_model=UiPathPrepareResponse,
    tags=["remediation", "uipath"],
)
def remediation_prepare_uipath(
    store: RemediationStoreDep,
    _: Guard,
    case_id: str,
) -> UiPathPrepareResponse:
    """Create one short-lived attended-browser launch after both approvals.

    The raw token appears only in this response. The database stores its
    digest, and the browser endpoint still re-verifies the signed envelope,
    Maker/Checker identities, allow-list, expected old value and idempotency.
    """

    case = _require_remediation_case(store, case_id)
    decision = PolicyDecision.model_validate(case["policy_decision"])
    if decision.outcome != "ELIGIBLE_FOR_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"case is not eligible for execution (outcome={decision.outcome})",
        )
    _approvals, maker, checker = _load_approvals(store, case_id)
    envelope = _load_or_issue_envelope(
        store, case=case, decision=decision, maker=maker, checker=checker
    )
    credential = issue_launch_credential()
    expires_at = min(envelope.expires_at, datetime.now(UTC) + timedelta(minutes=15))
    store.insert_uipath_event(
        run_id=credential.run_id,
        case_id=case_id,
        event_type="PREPARED",
        project_name=UIPATH_PROJECT_NAME,
        token_digest=credential.token_digest,
        expires_at=expires_at,
    )
    base_url = os.getenv("TRADEOPS_UIPATH_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    query = urlencode({"token": credential.token})
    return UiPathPrepareResponse(
        run_id=credential.run_id,
        case_id=case_id,
        project_name=UIPATH_PROJECT_NAME,
        launch_url=f"{base_url}/legacy/uipath/{credential.run_id}?{query}",
        expires_at=expires_at,
    )


def _require_uipath_launch(
    store: RemediationStore,
    *,
    run_id: str,
    token: str,
) -> dict[str, Any]:
    run = store.get_uipath_prepared_run(run_id)
    if run is None or not token_matches(token, str(run["token_digest"])):
        raise HTTPException(status_code=404, detail="attended run not found")
    if datetime.now(UTC) >= run["expires_at"]:
        raise HTTPException(status_code=410, detail="attended run has expired")
    return run


def _legacy_html(*, title: str, body: str, status_code: int = 200) -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 780px; margin: 48px auto;
            padding: 0 20px; color: #172033; background: #f5f7fb; }}
    main {{ background: white; border: 1px solid #dbe2ee; border-radius: 14px;
            padding: 28px; box-shadow: 0 8px 30px #17203312; }}
    h1 {{ margin-top: 0; }}
    dl {{ display: grid; grid-template-columns: 190px 1fr; gap: 10px 18px; }}
    dt {{ font-weight: 650; color: #526078; }} dd {{ margin: 0; }}
    code {{ overflow-wrap: anywhere; }}
    .ready, .success {{ color: #08783e; font-weight: 750; }}
    .noop {{ color: #825500; font-weight: 750; }}
    button {{ margin-top: 22px; padding: 12px 18px; border: 0; border-radius: 8px;
              color: white; background: #1769e0; font-weight: 700; cursor: pointer; }}
    .note {{ margin-top: 20px; color: #526078; font-size: 14px; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""
    return HTMLResponse(
        content=document,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
            ),
        },
    )


@app.get("/legacy/uipath/{run_id}", response_class=HTMLResponse, tags=["uipath"])
def uipath_legacy_screen(
    store: RemediationStoreDep,
    run_id: str,
    token: Annotated[str, Query(min_length=20)],
) -> HTMLResponse:
    """Render the intentionally small UI-only mock legacy booking screen."""

    run = _require_uipath_launch(store, run_id=run_id, token=token)
    _require_remediation_case(store, str(run["case_id"]))
    envelope_row = store.get_envelope(str(run["case_id"]))
    assert envelope_row is not None
    envelope = ActionEnvelope.model_validate(envelope_row["envelope_document"])
    record = store.read_legacy_booking_record(
        tenant_id=envelope.tenant_id,
        portfolio_id=envelope.portfolio_id,
        trade_id=envelope.trade_id,
    )
    if record is None:
        raise HTTPException(status_code=409, detail="legacy booking record not found")
    apply_query = urlencode({"token": token, "robot_reference": "uipath-studio-web-attended"})
    trade_id = escape(envelope.trade_id)
    portfolio_id = escape(envelope.portfolio_id)
    field_path = escape(envelope.field_path)
    current_value = escape(str(record["base_amount_value"]))
    expected_old_value = escape(envelope.expected_old_value)
    approved_value = escape(envelope.approved_value)
    maker_identity = escape(envelope.maker_identity)
    checker_identity = escape(envelope.checker_identity)
    body = f"""
<p id="screen-status" class="ready" data-testid="screen-status">READY FOR ATTENDED RUN</p>
<h1>Mock Legacy Booking</h1>
<dl>
  <dt>Trade ID</dt><dd id="trade-id" data-testid="trade-id"><code>{trade_id}</code></dd>
  <dt>Portfolio</dt><dd id="portfolio-id">{portfolio_id}</dd>
  <dt>Approved field</dt><dd id="field-path"><code>{field_path}</code></dd>
  <dt>Current value</dt><dd id="current-value" data-testid="current-value">
    {current_value}</dd>
  <dt>Expected old value</dt><dd id="expected-old-value" data-testid="expected-old-value">
    {expected_old_value}</dd>
  <dt>Approved value</dt><dd id="approved-value" data-testid="approved-value">
    {approved_value}</dd>
  <dt>Maker</dt><dd>{maker_identity}</dd>
  <dt>Checker</dt><dd>{checker_identity}</dd>
</dl>
<form method="post" action="/legacy/uipath/{escape(run_id)}/apply?{escape(apply_query)}">
  <button id="apply-approved-correction" data-testid="apply-approved-correction"
          type="submit">Apply approved correction</button>
</form>
<p class="note">The server re-verifies the signed envelope, two-person approval,
expected old value and idempotency key before any write.</p>
"""
    return _legacy_html(title="TradeOps Legacy Booking", body=body)


@app.post("/legacy/uipath/{run_id}/apply", response_class=HTMLResponse, tags=["uipath"])
def uipath_apply_approved_correction(
    adapter: Adapter,
    store: RemediationStoreDep,
    executor: ExecutorDep,
    run_id: str,
    token: Annotated[str, Query(min_length=20)],
    robot_reference: Annotated[str, Query(min_length=1, max_length=200)],
) -> HTMLResponse:
    """Execute through the same signed-envelope boundary used by the API."""

    run = _require_uipath_launch(store, run_id=run_id, token=token)
    case_id = str(run["case_id"])
    case = _require_remediation_case(store, case_id)
    decision = PolicyDecision.model_validate(case["policy_decision"])
    approvals, maker, checker = _load_approvals(store, case_id)
    envelope = _load_or_issue_envelope(
        store, case=case, decision=decision, maker=maker, checker=checker
    )
    store.insert_uipath_event(
        run_id=run_id,
        case_id=case_id,
        event_type="STARTED",
        project_name=str(run["project_name"]),
        robot_reference=robot_reference,
    )
    result = executor.execute(envelope, approvals)
    store.insert_uipath_event(
        run_id=run_id,
        case_id=case_id,
        event_type="COMPLETED",
        project_name=str(run["project_name"]),
        robot_reference=robot_reference,
        outcome=result.outcome,
        detail=result.detail,
        read_back_value=result.read_back_value,
        applied=result.applied,
    )
    if result.applied:
        _verify_after_applied_action(adapter, store, case=case, envelope=envelope)

    css_class = "success" if result.outcome == "SUCCESS" else "noop"
    escaped_outcome = escape(result.outcome)
    escaped_read_back = escape(result.read_back_value or "")
    body = f"""
<p id="execution-status" class="{css_class}" data-testid="execution-status">COMPLETED</p>
<h1>UiPath execution receipt</h1>
<dl>
  <dt>Run ID</dt><dd id="run-id"><code>{escape(run_id)}</code></dd>
  <dt>Outcome</dt><dd id="execution-outcome" data-testid="execution-outcome">
    {escaped_outcome}</dd>
  <dt>Applied</dt><dd id="execution-applied">{str(result.applied).lower()}</dd>
  <dt>Read-back value</dt><dd id="read-back-value" data-testid="read-back-value">
    {escaped_read_back}</dd>
  <dt>Robot reference</dt><dd>{escape(robot_reference)}</dd>
</dl>
<p class="note">{escape(result.detail)}</p>
"""
    return _legacy_html(title="UiPath Execution Receipt", body=body)


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
