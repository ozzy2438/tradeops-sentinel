-- Post-MVP explainable priority extension.
--
-- One immutable LightGBM+SHAP assessment is stored beside each newly
-- generated remediation case. It is deliberately separate from the
-- deterministic policy decision: model output can order a queue, but it
-- cannot approve, compile, sign, or execute an action.

BEGIN;

CREATE TABLE IF NOT EXISTS remediation_priority_assessments (
    row_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id             TEXT NOT NULL UNIQUE REFERENCES remediation_cases (case_id),
    model_provider      TEXT NOT NULL CHECK (model_provider = 'lightgbm'),
    model_version       TEXT NOT NULL,
    feature_version     TEXT NOT NULL,
    assessment_document JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE remediation_priority_assessments IS
    'Append-only advisory LightGBM priority scores and complete local SHAP '
    'contributions. Never an action-authorisation input.';

DROP TRIGGER IF EXISTS remediation_priority_assessments_no_update
    ON remediation_priority_assessments;
CREATE TRIGGER remediation_priority_assessments_no_update
    BEFORE UPDATE ON remediation_priority_assessments
    FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation();

DROP TRIGGER IF EXISTS remediation_priority_assessments_no_delete
    ON remediation_priority_assessments;
CREATE TRIGGER remediation_priority_assessments_no_delete
    BEFORE DELETE ON remediation_priority_assessments
    FOR EACH ROW EXECUTE FUNCTION reject_remediation_mutation();

DROP TRIGGER IF EXISTS remediation_priority_assessments_no_truncate
    ON remediation_priority_assessments;
CREATE TRIGGER remediation_priority_assessments_no_truncate
    BEFORE TRUNCATE ON remediation_priority_assessments
    FOR EACH STATEMENT EXECUTE FUNCTION reject_remediation_mutation();

COMMIT;
