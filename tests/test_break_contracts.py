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

    assert spot.product_type == "FX_SPOT"
    assert forward.product_type == "FX_FORWARD"
    assert resolved.state == "RESOLVED"
    assert resolved.resolution is not None
    assert resolved.resolution.resolution_type == "RECONCILIATION_PASS"


def test_missing_confirmation_source_uses_medium_severity() -> None:
    document = _break("trade-break-missing-execution.json")
    document["severity_context"] = "CONFIRMATION"
    document["severity"] = "MEDIUM"
    document["priority"]["ordering_key"] = [2, 3, 1, -3600]

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


def test_resolved_break_supports_human_non_action_and_rejects_agent_approval() -> None:
    document = _break("trade-break-resolved.json")
    document["evidence"][2]["role"] = "DISPOSITION_APPROVAL"
    document["resolution"] = {
        "resolution_type": "OWNER_APPROVED_NON_ACTION",
        "reconciliation_run_id": None,
        "disposition_id": "disp_date_001",
        "approver": {"identity_type": "HUMAN", "actor_id": "human_owner"},
        "evidence_ids": ["evidence_reconciliation_pass_001"],
    }

    _validate_schema("trade-break", document)
    validated = validate_contract_document("trade-break", document)
    assert validated.resolution is not None
    assert validated.resolution.resolution_type == "OWNER_APPROVED_NON_ACTION"

    document["resolution"]["approver"]["identity_type"] = "AGENT"
    with pytest.raises(ValidationError):
        _validate_schema("trade-break", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-break", document)
