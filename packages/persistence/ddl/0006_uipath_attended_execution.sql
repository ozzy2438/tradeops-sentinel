-- Post-MVP attended UiPath execution evidence.
--
-- Each row is an immutable event. PREPARED stores only a digest of the
-- high-entropy launch token; STARTED and COMPLETED prove what the local
-- browser boundary attempted and read back. No UiPath or Azure credential is
-- stored here.

BEGIN;

CREATE TABLE IF NOT EXISTS uipath_execution_events (
    event_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            TEXT NOT NULL,
    case_id           TEXT NOT NULL REFERENCES remediation_cases (case_id),
    event_type        TEXT NOT NULL CHECK (event_type IN ('PREPARED', 'STARTED', 'COMPLETED')),
    token_digest      TEXT,
    expires_at        TIMESTAMPTZ,
    project_name      TEXT NOT NULL,
    execution_mode    TEXT NOT NULL CHECK (execution_mode = 'ATTENDED_COMMUNITY'),
    robot_reference   TEXT,
    outcome           TEXT,
    detail            TEXT,
    read_back_value   TEXT,
    applied           BOOLEAN,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uipath_prepared_event_shape CHECK (
        (event_type = 'PREPARED' AND token_digest IS NOT NULL AND expires_at IS NOT NULL
            AND outcome IS NULL AND applied IS NULL)
        OR
        (event_type = 'STARTED' AND token_digest IS NULL AND expires_at IS NULL
            AND outcome IS NULL AND applied IS NULL)
        OR
        (event_type = 'COMPLETED' AND token_digest IS NULL AND expires_at IS NULL
            AND outcome IS NOT NULL AND applied IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uipath_execution_one_prepared_event
    ON uipath_execution_events (run_id) WHERE event_type = 'PREPARED';

CREATE INDEX IF NOT EXISTS uipath_execution_case_events
    ON uipath_execution_events (case_id, occurred_at, event_id);

COMMENT ON TABLE uipath_execution_events IS
    'Append-only evidence for explicitly prepared attended UiPath Community '
    'runs. Raw launch tokens and credentials are never persisted.';

DROP TRIGGER IF EXISTS uipath_execution_events_no_update ON uipath_execution_events;
CREATE TRIGGER uipath_execution_events_no_update
    BEFORE UPDATE ON uipath_execution_events
    FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation();

DROP TRIGGER IF EXISTS uipath_execution_events_no_delete ON uipath_execution_events;
CREATE TRIGGER uipath_execution_events_no_delete
    BEFORE DELETE ON uipath_execution_events
    FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation();

DROP TRIGGER IF EXISTS uipath_execution_events_no_truncate ON uipath_execution_events;
CREATE TRIGGER uipath_execution_events_no_truncate
    BEFORE TRUNCATE ON uipath_execution_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_remediation_mutation();

COMMIT;
