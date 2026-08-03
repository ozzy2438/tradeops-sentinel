-- P1 production boundaries: append-only inbox + portfolio-safe canonical key.
--
-- This migration is deliberately separate from 0001 so an existing database
-- can be upgraded without rewriting migration history. It is safe to reapply:
-- triggers are replaced deterministically and key/index changes run only when
-- their installed definitions differ.

BEGIN;

CREATE OR REPLACE FUNCTION reject_source_event_inbox_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'source_event_inbox is append-only: % is not permitted (ADR-001)',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS source_event_inbox_no_update ON source_event_inbox;
CREATE TRIGGER source_event_inbox_no_update
    BEFORE UPDATE ON source_event_inbox
    FOR EACH ROW EXECUTE FUNCTION reject_source_event_inbox_mutation();

DROP TRIGGER IF EXISTS source_event_inbox_no_delete ON source_event_inbox;
CREATE TRIGGER source_event_inbox_no_delete
    BEFORE DELETE ON source_event_inbox
    FOR EACH ROW EXECUTE FUNCTION reject_source_event_inbox_mutation();

DO $$
DECLARE
    installed_definition TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid)
    INTO installed_definition
    FROM pg_constraint
    WHERE conrelid = 'canonical_trade_state_versions'::regclass
      AND conname = 'canonical_trade_state_versions_trade_version_key';

    IF installed_definition IS DISTINCT FROM
       'UNIQUE (tenant_id, portfolio_id, trade_id, canonical_state_version)' THEN
        ALTER TABLE canonical_trade_state_versions
            DROP CONSTRAINT IF EXISTS canonical_trade_state_versions_trade_version_key;
        ALTER TABLE canonical_trade_state_versions
            ADD CONSTRAINT canonical_trade_state_versions_trade_version_key
            UNIQUE (tenant_id, portfolio_id, trade_id, canonical_state_version);
    END IF;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    index_is_portfolio_scoped BOOLEAN;
BEGIN
    SELECT indexdef LIKE
        '%(tenant_id, portfolio_id, trade_id, canonical_state_version DESC)%'
    INTO index_is_portfolio_scoped
    FROM pg_indexes
    WHERE schemaname = current_schema()
      AND tablename = 'canonical_trade_state_versions'
      AND indexname = 'canonical_trade_state_versions_current_idx';

    IF index_is_portfolio_scoped IS DISTINCT FROM TRUE THEN
        DROP INDEX IF EXISTS canonical_trade_state_versions_current_idx;
        CREATE INDEX canonical_trade_state_versions_current_idx
            ON canonical_trade_state_versions
                (tenant_id, portfolio_id, trade_id, canonical_state_version DESC);
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE canonical_trade_state_versions IS
    'Append-only versioned canonical projection (charter §29, ADR-001). '
    'Every row is immutable once inserted; canonical_state_version only '
    'increases per portfolio-scoped trade. The current projection is the row '
    'with the highest version for (tenant_id, portfolio_id, trade_id).';

COMMIT;
