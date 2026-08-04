-- Product MVP runtime tables: reconciliation runs and detected trade breaks.
--
-- 0001/0002 cover source ingestion and the canonical projection. This migration
-- adds the two tables the visible product needs to persist reconciliation
-- output so the API/dashboard can query it after the fact.
--
-- Both tables are append-only for the same reason as the canonical projection:
-- a reconciliation result is evidence. A re-run inserts a new run row rather
-- than mutating a prior one, so historical results stay queryable and the
-- audit trail is never rewritten. Guards cover UPDATE, DELETE and TRUNCATE --
-- TRUNCATE does not fire row-level triggers, so it needs its own
-- statement-level guard (see 0002 for the same pattern).
--
-- Safe to reapply: tables use IF NOT EXISTS and triggers are replaced
-- deterministically.

BEGIN;

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    row_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    run_version             INTEGER NOT NULL,
    tenant_id               TEXT NOT NULL,
    portfolio_id            TEXT NOT NULL,
    -- Config provenance: which approved rule set produced this result.
    config_id               TEXT NOT NULL,
    config_version          TEXT NOT NULL,
    config_hash             TEXT NOT NULL,
    detection_rule_version  TEXT NOT NULL,
    -- Aggregates the dashboard reads without re-deriving them.
    trades_evaluated        INTEGER NOT NULL DEFAULT 0,
    observations_ingested   INTEGER NOT NULL DEFAULT 0,
    clean_trades            INTEGER NOT NULL DEFAULT 0,
    broken_trades           INTEGER NOT NULL DEFAULT 0,
    break_count             INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL
        CHECK (status IN ('COMPLETED', 'FAILED')),
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ NOT NULL,

    CONSTRAINT reconciliation_runs_run_version_key
        UNIQUE (tenant_id, portfolio_id, run_id, run_version)
);

COMMENT ON TABLE reconciliation_runs IS
    'Append-only reconciliation run ledger. One row per executed run; a re-run '
    'inserts a new row and never mutates a prior result.';

CREATE INDEX IF NOT EXISTS reconciliation_runs_recent_idx
    ON reconciliation_runs (tenant_id, portfolio_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS trade_breaks (
    row_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    break_id                TEXT NOT NULL,
    break_version           INTEGER NOT NULL,
    run_id                  TEXT NOT NULL,
    tenant_id               TEXT NOT NULL,
    portfolio_id            TEXT NOT NULL,
    correlation_id          TEXT NOT NULL,
    trade_id                TEXT NOT NULL,
    canonical_state_version INTEGER NOT NULL,
    product_type            TEXT NOT NULL
        CHECK (product_type IN ('FX_SPOT', 'FX_FORWARD')),
    break_family            TEXT NOT NULL
        CHECK (break_family IN (
            'MISSING_REQUIRED_SOURCE',
            'AMBIGUOUS_OR_UNMATCHED_LINKAGE',
            'DUPLICATE_SOURCE_CONFLICT',
            'CURRENCY_PAIR_OR_SIDE_MISMATCH',
            'ECONOMIC_VALUE_MISMATCH',
            'TRADE_OR_VALUE_DATE_MISMATCH',
            'LIFECYCLE_STATUS_MISMATCH',
            'POST_ACTION_VERIFICATION_FAILURE'
        )),
    condition_code          TEXT NOT NULL,
    severity                TEXT NOT NULL,
    state                   TEXT NOT NULL,
    detected_at             TIMESTAMPTZ NOT NULL,
    -- Full typed break document, so break detail can show expected/observed
    -- comparisons, evidence and provenance without re-running the engine.
    break_document          JSONB NOT NULL,
    source_version_set      JSONB NOT NULL,
    config_hash             TEXT NOT NULL,

    CONSTRAINT trade_breaks_identity_key
        UNIQUE (tenant_id, portfolio_id, break_id, break_version)
);

COMMENT ON TABLE trade_breaks IS
    'Append-only detected trade breaks. break_document holds the full typed '
    'TradeBreak contract so provenance and expected/observed comparisons stay '
    'queryable without re-execution.';

CREATE INDEX IF NOT EXISTS trade_breaks_run_idx
    ON trade_breaks (tenant_id, portfolio_id, run_id);
CREATE INDEX IF NOT EXISTS trade_breaks_trade_idx
    ON trade_breaks (tenant_id, portfolio_id, trade_id);
CREATE INDEX IF NOT EXISTS trade_breaks_family_idx
    ON trade_breaks (tenant_id, portfolio_id, break_family);

-- A delivery that collides with an existing identity/version but carries
-- DIFFERENT verified content is a DUPLICATE_SOURCE_CONFLICT (C-09). The inbox
-- must never overwrite the authoritative row, but the conflicting delivery is
-- itself evidence: reconciliation needs it to raise the typed break, and an
-- auditor needs to see what was actually received. It is therefore quarantined
-- here rather than discarded.
CREATE TABLE IF NOT EXISTS source_event_conflicts (
    row_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observation_id          TEXT NOT NULL,
    tenant_id               TEXT NOT NULL,
    portfolio_id            TEXT NOT NULL,
    correlation_id          TEXT NOT NULL,
    lineage_group_id        TEXT NOT NULL,
    source_system           TEXT NOT NULL,
    observation_kind        TEXT NOT NULL,
    source_business_key     TEXT NOT NULL,
    source_version          TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    conflicting_observation_id TEXT NOT NULL,
    conflicting_content_hash   TEXT NOT NULL,
    observation_document    JSONB NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT source_event_conflicts_identity_key
        UNIQUE (tenant_id, portfolio_id, observation_id)
);

COMMENT ON TABLE source_event_conflicts IS
    'Quarantined conflicting deliveries (C-09 DUPLICATE_SOURCE_CONFLICT). The '
    'authoritative source_event_inbox row is never overwritten; the rejected '
    'delivery is retained here as evidence for reconciliation and audit.';

CREATE INDEX IF NOT EXISTS source_event_conflicts_lineage_idx
    ON source_event_conflicts (tenant_id, portfolio_id, lineage_group_id);

CREATE OR REPLACE FUNCTION reject_product_runtime_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        '% is append-only: % is not permitted (charter §29, ADR-001)',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS reconciliation_runs_no_update ON reconciliation_runs;
CREATE TRIGGER reconciliation_runs_no_update
    BEFORE UPDATE ON reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS reconciliation_runs_no_delete ON reconciliation_runs;
CREATE TRIGGER reconciliation_runs_no_delete
    BEFORE DELETE ON reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS reconciliation_runs_no_truncate ON reconciliation_runs;
CREATE TRIGGER reconciliation_runs_no_truncate
    BEFORE TRUNCATE ON reconciliation_runs
    FOR EACH STATEMENT EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS trade_breaks_no_update ON trade_breaks;
CREATE TRIGGER trade_breaks_no_update
    BEFORE UPDATE ON trade_breaks
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS trade_breaks_no_delete ON trade_breaks;
CREATE TRIGGER trade_breaks_no_delete
    BEFORE DELETE ON trade_breaks
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS source_event_conflicts_no_update ON source_event_conflicts;
CREATE TRIGGER source_event_conflicts_no_update
    BEFORE UPDATE ON source_event_conflicts
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS source_event_conflicts_no_delete ON source_event_conflicts;
CREATE TRIGGER source_event_conflicts_no_delete
    BEFORE DELETE ON source_event_conflicts
    FOR EACH ROW EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS source_event_conflicts_no_truncate ON source_event_conflicts;
CREATE TRIGGER source_event_conflicts_no_truncate
    BEFORE TRUNCATE ON source_event_conflicts
    FOR EACH STATEMENT EXECUTE FUNCTION reject_product_runtime_mutation();

DROP TRIGGER IF EXISTS trade_breaks_no_truncate ON trade_breaks;
CREATE TRIGGER trade_breaks_no_truncate
    BEFORE TRUNCATE ON trade_breaks
    FOR EACH STATEMENT EXECUTE FUNCTION reject_product_runtime_mutation();

COMMIT;
