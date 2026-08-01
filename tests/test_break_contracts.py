"""TS-4 deterministic break taxonomy and lifecycle contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource

from packages.contracts.models import validate_contract_document

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "packages" / "contracts"
SCHEMAS = CONTRACTS / "schemas"
EXAMPLES = CONTRACTS / "examples"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        resources.append((path.name, Resource.from_contents(_load(path))))
    return Registry().with_resources(resources)


def _validate_schema(contract: str, document: dict[str, Any]) -> None:
    schema = _load(SCHEMAS / f"{contract}.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=FormatChecker(),
    ).validate(document)


def _break(filename: str) -> dict[str, Any]:
    return _load(EXAMPLES / "valid" / filename)


def test_manifest_fixture_matrix_covers_every_family_for_spot_and_forward() -> None:
    matrix = _load(EXAMPLES / "trade-break-fixture-matrix.json")
    manifest = _load(EXAMPLES / "manifest.json")
    expected_families = {
        "MISSING_REQUIRED_SOURCE",
        "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
        "DUPLICATE_SOURCE_CONFLICT",
        "CURRENCY_PAIR_OR_SIDE_MISMATCH",
        "ECONOMIC_VALUE_MISMATCH",
        "TRADE_OR_VALUE_DATE_MISMATCH",
        "LIFECYCLE_STATUS_MISMATCH",
        "POST_ACTION_VERIFICATION_FAILURE",
    }

    assert matrix["products"] == ["FX_SPOT", "FX_FORWARD"]
    assert {row["family"] for row in matrix["families"]} == expected_families
    for row in matrix["families"]:
        filename = row["positive_fixture"]
        assert manifest["valid"][filename] == "trade-break"
        base = _break(filename)
        assert base["family"] == row["family"]
        for product_type in matrix["products"]:
            document = copy.deepcopy(base)
            document["product_type"] = product_type
            _validate_schema("trade-break", document)
            validate_contract_document("trade-break", document)


@pytest.mark.parametrize(
    ("filename", "wrong_field_path", "wrong_value_type"),
    [
        ("trade-break-missing-execution.json", "/payload/lifecycle_status", "LIFECYCLE_STATUS"),
        ("trade-break-ambiguous-linkage.json", "/payload/lifecycle_status", "LIFECYCLE_STATUS"),
        ("trade-break-duplicate-source.json", "/payload/lifecycle_status", "LIFECYCLE_STATUS"),
        ("trade-break-currency-side.json", "/payload/base_amount", "DECIMAL"),
        ("trade-break-economic-forward.json", "/payload/lifecycle_status", "LIFECYCLE_STATUS"),
        ("trade-break-resolved.json", "/payload/lifecycle_status", "LIFECYCLE_STATUS"),
        ("trade-break-lifecycle-status.json", "/payload/base_amount", "DECIMAL"),
        ("trade-break-post-action.json", "/payload/value_date", "DATE"),
    ],
)
def test_family_comparison_matrix_rejects_cross_family_field_types(
    filename: str, wrong_field_path: str, wrong_value_type: str
) -> None:
    document = _break(filename)
    document["comparisons"][0]["field_path"] = wrong_field_path
    document["comparisons"][0]["value_type"] = wrong_value_type

    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_comparison_evidence_must_bind_to_the_same_field_path() -> None:
    document = _break("trade-break-economic-forward.json")
    document["evidence"][0]["field_path"] = "/payload/terms_amount"

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_missing_source_context_is_typed_and_trade_capture_is_rejected() -> None:
    document = _break("trade-break-missing-execution.json")
    document["severity_context"] = "TRADE_CAPTURE"

    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)

    document = _break("trade-break-missing-execution.json")
    document["missing_source_expectation"]["expected_by"] = "2026-08-01T08:59:59Z"
    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)

    document = _break("trade-break-missing-execution.json")
    document["evidence"][0]["captured_at"] = "2026-08-01T08:59:59Z"
    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_comparison_values_must_be_distinct() -> None:
    document = _break("trade-break-economic-forward.json")
    document["comparisons"][0]["observed_value"] = document["comparisons"][0]["expected_value"]

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_taxonomy_exposes_exact_eight_families_and_fourteen_transitions() -> None:
    taxonomy = validate_contract_document(
        "break-taxonomy", _load(EXAMPLES / "valid" / "break-taxonomy.json")
    )

    assert [family.family for family in taxonomy.families] == [
        "MISSING_REQUIRED_SOURCE",
        "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
        "DUPLICATE_SOURCE_CONFLICT",
        "CURRENCY_PAIR_OR_SIDE_MISMATCH",
        "ECONOMIC_VALUE_MISMATCH",
        "TRADE_OR_VALUE_DATE_MISMATCH",
        "LIFECYCLE_STATUS_MISMATCH",
        "POST_ACTION_VERIFICATION_FAILURE",
    ]
    assert len(taxonomy.allowed_transitions) == 14
    assert taxonomy.priority_policy.tie_breakers == [
        "MATERIALITY_BAND",
        "SEVERITY",
        "LIFECYCLE_DEADLINE",
        "CASE_AGE",
    ]


def test_spot_forward_and_resolved_break_examples_validate() -> None:
    spot = validate_contract_document("trade-break", _break("trade-break-missing-execution.json"))
    forward = validate_contract_document("trade-break", _break("trade-break-economic-forward.json"))
    resolved = validate_contract_document("trade-break", _break("trade-break-resolved.json"))
    reopened = validate_contract_document("trade-break", _break("trade-break-reopened.json"))

    assert spot.product_type == "FX_SPOT"
    assert forward.product_type == "FX_FORWARD"
    assert resolved.state == "RESOLVED"
    assert resolved.resolution is not None
    assert resolved.resolution.resolution_type == "RECONCILIATION_PASS"
    assert reopened.break_version == 2
    assert reopened.supersedes_break_id == "break_value_date_resolved_001"
    assert reopened.state == "OPEN"


def test_missing_confirmation_source_uses_medium_severity() -> None:
    document = _break("trade-break-missing-execution.json")
    document["severity_context"] = "CONFIRMATION"
    document["severity"] = "MEDIUM"
    document["priority"]["ordering_key"] = [2, 3, 1, -3600]
    document["evaluated_field_paths"] = ["/payload/confirmation_status"]
    document["comparisons"][0]["field_path"] = "/payload/confirmation_status"
    document["evidence"][0]["field_path"] = "/payload/confirmation_status"
    document["evidence"][1]["field_path"] = "/payload/confirmation_status"
    document["missing_source_expectation"] = {
        "expected_observation_kind": "CONFIRMATION",
        "expected_source_system": "FPML_CONFIRMATION",
        "field_path": "/payload/confirmation_status",
        "arrival_window_rule_version": "1.0.0",
        "watermark_at": "2026-08-01T09:00:00Z",
        "expected_by": "2026-08-01T09:00:00Z",
    }

    _validate_schema("trade-break", document)
    validated = validate_contract_document("trade-break", document)
    assert validated.severity == "MEDIUM"


def test_priority_key_is_not_free_form() -> None:
    document = _break("trade-break-missing-execution.json")
    document["priority"]["ordering_key"] = [2, 2, 1, 0]

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_comparison_paths_must_match_evaluated_paths() -> None:
    document = _break("trade-break-economic-forward.json")
    document["evaluated_field_paths"] = ["/payload/terms_amount"]

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_duplicate_source_ids_are_rejected_before_last_write_wins() -> None:
    document = _break("trade-break-economic-forward.json")
    duplicate = copy.deepcopy(document["source_version_set"][0])
    duplicate["source_version"] = "2"
    duplicate["content_hash"] = (
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    document["source_version_set"].append(duplicate)

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_cross_scope_source_is_rejected_by_semantic_layer() -> None:
    document = _break("trade-break-missing-execution.json")
    document["source_version_set"][0]["source_tenant_id"] = "tenant_other"

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_invalid_transition_cannot_skip_verification() -> None:
    document = _break("trade-break-resolved.json")
    document["previous_state"] = "OPEN"
    document["resolved_at"] = None
    document["resolution"] = None

    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_taxonomy_condition_drift_fails_closed_in_both_layers() -> None:
    document = _load(EXAMPLES / "valid" / "break-taxonomy.json")
    document["families"][4]["condition_code"] = "EXACT_CURRENCY_PAIR_SIDE"

    with pytest.raises(ValidationError):
        _validate_schema("break-taxonomy", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("break-taxonomy", document)


def test_taxonomy_order_drift_fails_closed_in_both_layers() -> None:
    document = _load(EXAMPLES / "valid" / "break-taxonomy.json")
    document["families"][0], document["families"][1] = (
        document["families"][1],
        document["families"][0],
    )
    document["allowed_transitions"][0], document["allowed_transitions"][1] = (
        document["allowed_transitions"][1],
        document["allowed_transitions"][0],
    )

    with pytest.raises(ValidationError):
        _validate_schema("break-taxonomy", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("break-taxonomy", document)


def test_date_break_requires_reconciliation_and_rejects_non_action() -> None:
    document = _break("trade-break-resolved.json")
    document["evidence"][2]["role"] = "DISPOSITION_APPROVAL"
    document["resolution"] = {
        "resolution_type": "OWNER_APPROVED_NON_ACTION",
        "reconciliation_run_id": None,
        "disposition_id": "disp_date_001",
        "approver": {"identity_type": "HUMAN", "actor_id": "human_owner"},
        "evidence_ids": ["evidence_reconciliation_pass_001"],
        "evidence_roles": ["DISPOSITION_APPROVAL"],
    }

    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_missing_source_supports_human_non_action() -> None:
    document = _break("trade-break-missing-execution.json")
    document["evidence"].append(
        {
            "evidence_id": "evidence_missing_disposition_001",
            "role": "DISPOSITION_APPROVAL",
            "content_hash": (
                "sha256:4444444444444444444444444444444444444444444444444444444444444444"
            ),
            "captured_at": "2026-08-01T09:00:30Z",
            "source_observation_id": None,
            "source_version": None,
            "field_path": None,
        }
    )
    document["state"] = "RESOLVED"
    document["previous_state"] = "VERIFYING"
    document["transition_reason"] = "RESOLUTION_VERIFIED"
    document["resolved_at"] = "2026-08-01T09:01:00Z"
    document["resolution"] = {
        "resolution_type": "OWNER_APPROVED_NON_ACTION",
        "reconciliation_run_id": None,
        "disposition_id": "disp_missing_001",
        "approver": {"identity_type": "HUMAN", "actor_id": "human_owner"},
        "evidence_ids": ["evidence_missing_disposition_001"],
        "evidence_roles": ["DISPOSITION_APPROVAL"],
    }

    _validate_schema("trade-break", document)
    validated = validate_contract_document("trade-break", document)
    assert validated.resolution is not None
    assert validated.resolution.resolution_type == "OWNER_APPROVED_NON_ACTION"


def test_resolution_evidence_ids_bind_to_known_roles_and_are_unique() -> None:
    document = _break("trade-break-resolved.json")
    document["resolution"]["evidence_ids"] = ["evidence_date_compare_001"]
    document["resolution"]["evidence_roles"] = ["RECONCILIATION_RESULT"]

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)

    document = _break("trade-break-resolved.json")
    document["resolution"]["evidence_ids"] = [
        "evidence_reconciliation_pass_001",
        "evidence_reconciliation_pass_001",
    ]
    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_resolution_evidence_must_exist_before_resolved_at() -> None:
    document = _break("trade-break-resolved.json")
    document["resolved_at"] = "2026-08-01T11:04:30Z"

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)


def test_reopened_break_must_mint_a_new_record_id() -> None:
    document = _break("trade-break-reopened.json")
    document["supersedes_break_id"] = document["break_id"]

    _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)
