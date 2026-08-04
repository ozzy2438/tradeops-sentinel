-- Minimum controlled-AI remediation slice: detect -> AI recommend -> cite
-- runbook -> policy decision -> Maker approve -> Checker approve -> signed
-- envelope -> mock legacy booking update -> read-back -> reconciliation
-- rerun -> evidence.
--
-- legacy_booking_records is the ONE mutable table added here: it
-- deliberately models an external legacy system's current-state record, not
-- this project's own append-only audit trail. Every other table below is
-- append-only, consistent with 0001-0003, and enforced the same way
-- (statement-level TRUNCATE guards alongside row-level UPDATE/DELETE guards,
-- since row-level triggers never fire for TRUNCATE).
--
-- Safe to reapply: tables use IF NOT EXISTS and triggers are replaced
-- deterministically.

BEGIN;

-- The mock legacy booking system's current record for one trade. Written
-- only by MockLegacyBookingAdapter, under a verified, signed envelope.
-- last_applied_idempotency_key is how a replayed execution attempt is
-- recognised and produces no second write (RB-003).
CREATE TABLE IF NOT EXISTS legacy_booking_records (
    tenant_id                    TEXT NOT NULL,
    portfolio_id                 TEXT NOT NULL,
    trade_id                     TEXT NOT NULL,
    base_amount_value            TEXT NOT NULL,
    base_amount_currency         TEXT NOT NULL,
    base_amount_scale            INTEGER NOT NULL,
    source_observation_id        TEXT NOT NULL,
    booking_version               INTEGER NOT NULL DEFAULT 1,
    last_applied_idempotency_key  TEXT,
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, portfolio_id, trade_id)
);

COMMENT ON TABLE legacy_booking_records IS
    'Mock legacy booking system current-state record -- the only mutable '
    'table in the remediation slice. Models an external system boundary, '
    'not this project''s own audit trail. Written only by '
    'MockLegacyBookingAdapter under a verified signed envelope.';

-- Append-only: one row per generated AI recommendation + deterministic
-- policy decision. Never mutated after insert; later stages are recorded in
-- the child tables below rather than by updating this row.
CREATE TABLE IF NOT EXISTS remediation_cases (
    row_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id            TEXT NOT NULL UNIQUE,
    break_id           TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    trade_id           TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    portfolio_id       TEXT NOT NULL,
    product_type       TEXT NOT NULL,
    ai_provider        TEXT NOT NULL,
    break_facts        JSONB NOT NULL,
    ai_recommendation  JSONB NOT NULL,
    policy_decision    JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE remediation_cases IS
    'Append-only: one row per generated AI recommendation + deterministic '
    'policy decision. Never mutated after insert.';

CREATE INDEX IF NOT EXISTS remediation_cases_break_idx
    ON remediation_cases (tenant_id, portfolio_id, break_id);

-- Append-only. Unique on (case_id, role): exactly one Maker decision and one
-- Checker decision per case, ever -- a second submission for the same role
-- is rejected at the database boundary, not only in application code.
CREATE TABLE IF NOT EXISTS remediation_approvals (
    row_id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id                       TEXT NOT NULL REFERENCES remediation_cases (case_id),
    role                          TEXT NOT NULL CHECK (role IN ('MAKER', 'CHECKER')),
    approver_identity             TEXT NOT NULL,
    decision                      TEXT NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
    approved_recommendation_hash  TEXT NOT NULL,
    decided_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT remediation_approvals_one_per_role UNIQUE (case_id, role)
);

COMMENT ON TABLE remediation_approvals IS
    'Append-only. Unique on (case_id, role): exactly one Maker decision and '
    'one Checker decision per case, ever.';

-- Append-only. One signed envelope per case_id (unique), issued once and
-- reused -- by idempotency_key -- for every subsequent execution attempt
-- against that case.
CREATE TABLE IF NOT EXISTS remediation_envelopes (
    row_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id            TEXT NOT NULL UNIQUE REFERENCES remediation_cases (case_id),
    idempotency_key    TEXT NOT NULL UNIQUE,
    envelope_document  JSONB NOT NULL,
    content_hash       TEXT NOT NULL,
    issued_at          TIMESTAMPTZ NOT NULL,
    expires_at         TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE remediation_envelopes IS
    'Append-only. One signed envelope per case_id (unique), issued once and '
    'reused for every execution attempt against that case.';

-- Append-only. One row per EXECUTION ATTEMPT, not per case: multiple
-- attempts are expected and are exactly how idempotent-replay and
-- timeout-recovery are evidenced. At most one row per idempotency_key may
-- ever have applied = true.
CREATE TABLE IF NOT EXISTS remediation_executions (
    row_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id          TEXT NOT NULL REFERENCES remediation_cases (case_id),
    idempotency_key  TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    detail           TEXT NOT NULL,
    read_back_value  TEXT,
    applied          BOOLEAN NOT NULL,
    attempted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE remediation_executions IS
    'Append-only. One row per execution attempt against a case -- multiple '
    'attempts are how idempotent-replay/timeout-recovery is evidenced; at '
    'most one row per idempotency_key may have applied = true.';

CREATE INDEX IF NOT EXISTS remediation_executions_case_idx
    ON remediation_executions (case_id);

-- Append-only. Frozen, hashed evidence snapshot written once a case reaches
-- a terminal state (resolved, or terminally rejected/abstained).
CREATE TABLE IF NOT EXISTS remediation_evidence (
    row_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evidence_id        TEXT NOT NULL UNIQUE,
    case_id            TEXT NOT NULL UNIQUE REFERENCES remediation_cases (case_id),
    evidence_document  JSONB NOT NULL,
    content_hash       TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE remediation_evidence IS
    'Append-only. One frozen, hashed evidence snapshot per case_id, written '
    'once the case reaches a terminal state.';

-- Append-only enforcement, matching 0001-0003, for every table above except
-- legacy_booking_records (deliberately mutable -- see its own comment).
CREATE OR REPLACE FUNCTION reject_remediation_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        '% is append-only: % is not permitted (charter §29, ADR-001)',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    append_only_table TEXT;
BEGIN
    FOREACH append_only_table IN ARRAY ARRAY[
        'remediation_cases',
        'remediation_approvals',
        'remediation_envelopes',
        'remediation_executions',
        'remediation_evidence'
    ]
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I_no_update ON %I',
            append_only_table, append_only_table
        );
        EXECUTE format(
            'CREATE TRIGGER %I_no_update BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation()',
            append_only_table, append_only_table
        );
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I_no_delete ON %I',
            append_only_table, append_only_table
        );
        EXECUTE format(
            'CREATE TRIGGER %I_no_delete BEFORE DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation()',
            append_only_table, append_only_table
        );
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I_no_truncate ON %I',
            append_only_table, append_only_table
        );
        EXECUTE format(
            'CREATE TRIGGER %I_no_truncate BEFORE TRUNCATE ON %I '
            'FOR EACH STATEMENT EXECUTE FUNCTION reject_remediation_mutation()',
            append_only_table, append_only_table
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMIT;
