-- TS-10: source_event_inbox + canonical_trade_state_versions
--
-- Scope (issue #10 / charter §29 C-09): persistence/inbox only. No
-- reconciliation logic (TS-11), no generator changes, no contract changes.
-- Column names and constraints mirror packages/contracts/models.py exactly
-- (ObservationEnvelope, CanonicalTradeState, FieldProvenanceMap) so the
-- Python assembler and this schema cannot silently drift apart.
--
-- Consistency item C-09: source_event_inbox is unique on the stable
-- source-family identity + version — (tenant_id, portfolio_id,
-- source_system, observation_kind, source_business_key, source_version)
-- — NOT on content_hash, and NOT on (source_system, source_event_id),
-- which is per-delivery identity (ADR-001/identity-policy): a
-- retransmitted or replayed delivery gets a new source_event_id even
-- when it carries the same logical revision. A second insert with the
-- same identity/version and the SAME content_hash is an idempotent
-- replay (no-op). A second insert with the same identity/version and a
-- DIFFERENT content_hash is a DUPLICATE_SOURCE_CONFLICT and must be
-- rejected deterministically, never silently overwritten or duplicated.
-- source_event_id remains a plain column (per-delivery identity is not
-- discarded), but it is deliberately NOT part of this constraint.

BEGIN;

CREATE TABLE IF NOT EXISTS source_event_inbox (
    inbox_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- schema_version, entity_version, and actor mirror ObservationEnvelope
    -- (packages/contracts/models.py) exactly, alongside payload, so a row
    -- can reconstruct the full source envelope, not just its FxPayload
    -- (Honey, 2026-08-02T23:46, finding 1).
    schema_version        TEXT NOT NULL,
    observation_id       TEXT NOT NULL,
    observation_kind     TEXT NOT NULL
        CHECK (observation_kind IN ('EXECUTION', 'TRADE_CAPTURE', 'CONFIRMATION', 'BOOKING')),
    entity_version         INTEGER NOT NULL CHECK (entity_version = 1),
    tenant_id            TEXT NOT NULL,
    portfolio_id         TEXT NOT NULL,
    correlation_id       TEXT NOT NULL,
    source_system        TEXT NOT NULL
        CHECK (source_system IN
            ('FIX_EXECUTION', 'FIX_TRADE_CAPTURE', 'FPML_CONFIRMATION', 'MOCK_LEGACY_BOOKING')),
    -- observation_kind and source_system must be the ADR-001-defined pair
    -- (EXECUTION/FIX_EXECUTION, TRADE_CAPTURE/FIX_TRADE_CAPTURE,
    -- CONFIRMATION/FPML_CONFIRMATION, BOOKING/MOCK_LEGACY_BOOKING), exactly
    -- as the Pydantic ExecutionObservation/TradeCaptureObservation/
    -- ConfirmationObservation/BookingObservation subclasses enforce.
    -- Independent per-column CHECKs alone would allow impossible pairs
    -- such as (EXECUTION, FPML_CONFIRMATION) (Honey, 2026-08-02T23:46,
    -- finding 2); this coupled CHECK closes that gap at the DB level too.
    CONSTRAINT source_event_inbox_kind_system_pair CHECK (
        (observation_kind = 'EXECUTION' AND source_system = 'FIX_EXECUTION')
        OR (observation_kind = 'TRADE_CAPTURE' AND source_system = 'FIX_TRADE_CAPTURE')
        OR (observation_kind = 'CONFIRMATION' AND source_system = 'FPML_CONFIRMATION')
        OR (observation_kind = 'BOOKING' AND source_system = 'MOCK_LEGACY_BOOKING')
    ),
    source_event_id      TEXT NOT NULL,
    source_business_key  TEXT NOT NULL,
    source_version       TEXT NOT NULL,
    -- content_hash is stored SEPARATELY from the unique key on purpose
    -- (C-09) — it is compared in application/trigger logic to distinguish
    -- idempotent replay from DUPLICATE_SOURCE_CONFLICT, never folded into
    -- the uniqueness constraint itself.
    content_hash          TEXT NOT NULL,
    event_time             TIMESTAMPTZ NOT NULL,
    effective_time         TIMESTAMPTZ NOT NULL,
    ingest_time             TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_sequence        INTEGER NOT NULL,
    lineage_group_id       TEXT NOT NULL,
    actor                   JSONB NOT NULL,
    supersedes_observation_id TEXT,
    supersession_reason     TEXT
        CHECK (supersession_reason IN ('CORRECTION', 'LATE_REVISION', 'SOURCE_AMENDMENT')),
    payload               JSONB NOT NULL,
    received_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- C-09: unique on the stable source-family identity + version.
    CONSTRAINT source_event_inbox_identity_version_key
        UNIQUE (tenant_id, portfolio_id, source_system, observation_kind,
                source_business_key, source_version)
);

COMMENT ON TABLE source_event_inbox IS
    'Transactional inbox (ADR-008 §2). Unique on the stable source-family '
    'identity + version (tenant_id, portfolio_id, source_system, '
    'observation_kind, source_business_key, source_version) — not on '
    'delivery identity (source_system, source_event_id), which changes on '
    'retransmission/replay. content_hash is a separate column used to '
    'distinguish idempotent replay from DUPLICATE_SOURCE_CONFLICT, per '
    'consistency item C-09.';

-- Delivery-level identity is modelled separately (Honey, 2026-08-02T23:34)
-- and must never substitute for the identity/version conflict key above:
-- it is an evidence/audit index, not a uniqueness gate.
CREATE INDEX IF NOT EXISTS source_event_inbox_delivery_idx
    ON source_event_inbox (source_system, source_event_id);

CREATE INDEX IF NOT EXISTS source_event_inbox_lineage_idx
    ON source_event_inbox (lineage_group_id);

-- Append-only, versioned canonical projection. No UPDATE or DELETE is ever
-- issued against this table by the assembler: late arrivals and
-- corrections always INSERT the next canonical_state_version, so every
-- prior version remains queryable (no destructive overwrite).
CREATE TABLE IF NOT EXISTS canonical_trade_state_versions (
    row_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- schema_version mirrors CanonicalTradeState.schema_version
    -- (packages/contracts/models.py) (Honey, 2026-08-02T23:46, finding 1).
    schema_version           TEXT NOT NULL,
    trade_id                 TEXT NOT NULL,
    entity_version            INTEGER NOT NULL,
    canonical_state_version   INTEGER NOT NULL,
    tenant_id                 TEXT NOT NULL,
    portfolio_id              TEXT NOT NULL,
    correlation_id            TEXT NOT NULL,
    content_hash              TEXT NOT NULL,
    as_of_time                 TIMESTAMPTZ NOT NULL,
    source_watermark           TIMESTAMPTZ NOT NULL,
    source_version_set         JSONB NOT NULL,
    actor                      JSONB NOT NULL,
    state                      JSONB NOT NULL,
    field_provenance           JSONB NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT canonical_trade_state_versions_trade_version_key
        UNIQUE (tenant_id, trade_id, canonical_state_version)
);

COMMENT ON TABLE canonical_trade_state_versions IS
    'Append-only versioned canonical projection (charter §29, ADR-001). '
    'Every row is immutable once inserted; canonical_state_version only '
    'increases per trade_id. The "current" projection is the row with the '
    'highest canonical_state_version for a given (tenant_id, trade_id).';

CREATE INDEX IF NOT EXISTS canonical_trade_state_versions_current_idx
    ON canonical_trade_state_versions (tenant_id, trade_id, canonical_state_version DESC);

-- Append-only enforcement at the database boundary, not just in comments
-- (Fizz, 2026-08-02T23:58, finding 2; confirmed by Honey, 2026-08-03T00:00).
-- A fresh install previously had no trigger or privilege boundary
-- preventing UPDATE/DELETE on canonical_trade_state_versions. A
-- BEFORE UPDATE/DELETE trigger is used rather than a role/GRANT scheme:
-- TS-10 does not define application roles (that is a broader concern —
-- see ADR-008 §3's action_gateway_writer pattern for a different table),
-- and a trigger enforces insert-only for every role/session uniformly,
-- with no additional setup required to exercise it in a fresh database.
CREATE OR REPLACE FUNCTION reject_canonical_trade_state_versions_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'canonical_trade_state_versions is append-only: % is not permitted (charter §29, ADR-001)',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS canonical_trade_state_versions_no_update
    ON canonical_trade_state_versions;
CREATE TRIGGER canonical_trade_state_versions_no_update
    BEFORE UPDATE ON canonical_trade_state_versions
    FOR EACH ROW EXECUTE FUNCTION reject_canonical_trade_state_versions_mutation();

DROP TRIGGER IF EXISTS canonical_trade_state_versions_no_delete
    ON canonical_trade_state_versions;
CREATE TRIGGER canonical_trade_state_versions_no_delete
    BEFORE DELETE ON canonical_trade_state_versions
    FOR EACH ROW EXECUTE FUNCTION reject_canonical_trade_state_versions_mutation();

COMMIT;
