"""TradeOps Sentinel product API.

A deliberately small FastAPI surface over the existing deterministic core.
Nine endpoints, one API-key guard, typed errors. No registration, OAuth, JWT
or RBAC -- those are explicitly out of scope for this MVP.
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

from .service import (
    DEMO_PORTFOLIO_IDS,
    DEMO_TENANT_ID,
    SCOPE,
    DemoScopeError,
    build_summary,
    load_demo_corpus,
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
