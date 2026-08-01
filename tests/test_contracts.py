"""TS-3 contract tests for JSON Schema, Pydantic, and deterministic semantics."""

from __future__ import annotations

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


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest() -> dict[str, Any]:
    return _load_json(EXAMPLES / "manifest.json")


def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        resources.append((path.name, Resource.from_contents(_load_json(path))))
    return Registry().with_resources(resources)


def _validate_json_schema(contract: str, document: dict[str, Any]) -> None:
    schema = _load_json(SCHEMAS / f"{contract}.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=FormatChecker(),
    ).validate(document)


@pytest.mark.parametrize("filename,contract", _manifest()["valid"].items())
def test_valid_examples_pass_both_contract_layers(filename: str, contract: str) -> None:
    document = _load_json(EXAMPLES / "valid" / filename)

    _validate_json_schema(contract, document)
    validate_contract_document(contract, document)


@pytest.mark.parametrize("filename,expectation", _manifest()["invalid"].items())
def test_invalid_examples_reject_in_expected_layer(
    filename: str, expectation: dict[str, Any]
) -> None:
    contract = expectation["contract"]
    document = _load_json(EXAMPLES / "invalid" / filename)
    schema_error: ValidationError | None = None
    pydantic_error: PydanticValidationError | None = None

    try:
        _validate_json_schema(contract, document)
    except ValidationError as error:
        schema_error = error

    try:
        validate_contract_document(contract, document)
    except PydanticValidationError as error:
        pydantic_error = error

    assert (schema_error is not None) is expectation["schema_rejects"], expectation["reason"]
    assert (pydantic_error is not None) is expectation["pydantic_rejects"], expectation["reason"]


def test_replay_is_deterministic_for_canonical_record() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-spot.json")

    first = validate_contract_document("canonical-trade", document)
    second = validate_contract_document("canonical-trade", document)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.model_dump(mode="json")["content_hash"] == document["content_hash"]


def test_every_canonical_field_has_versioned_provenance() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-spot.json")
    canonical = validate_contract_document("canonical-trade", document)
    state = canonical.model_dump(mode="json")["state"]
    provenance = canonical.model_dump(mode="json")["field_provenance"]

    assert set(provenance) == set(state)
    for field_name, source in provenance.items():
        assert source["source_observation_id"]
        assert source["source_version"]
        assert source["normalisation_rule_version"] == "1.0.0"
        assert source["resolution_rule_version"] == "1.0.0"
        assert source["field_path"] == f"/payload/{field_name}"


def test_identity_policy_has_complete_ordered_outcome_table() -> None:
    document = _load_json(EXAMPLES / "valid" / "identity-policy.json")
    policy = validate_contract_document("identity-policy", document)

    assert [row.precedence for row in policy.outcomes] == list(range(1, 9))
    assert {row.outcome for row in policy.outcomes} == {
        "NEW_OBSERVATION",
        "IDEMPOTENT_REPLAY",
        "DUPLICATE_SOURCE_CONFLICT",
        "NEW_SOURCE_VERSION",
        "LATE_SOURCE_VERSION_RECORDED",
        "REJECT_UNSUPPORTED_SCHEMA_VERSION",
        "REJECT_CROSS_PORTFOLIO_LINKAGE",
        "LINKAGE_REVIEW_REQUIRED",
    }


def test_source_of_truth_policy_has_unique_field_ownership() -> None:
    document = _load_json(EXAMPLES / "valid" / "source-of-truth-policy.json")
    policy = validate_contract_document("source-of-truth-policy", document)

    assert len(policy.field_rules) == len({rule.field_path for rule in policy.field_rules})
    for rule in policy.field_rules:
        assert set(rule.trusted_sources).issubset(rule.source_precedence)


def test_source_of_truth_policy_requires_exact_field_path_set() -> None:
    document = _load_json(EXAMPLES / "valid" / "source-of-truth-policy.json")
    document["field_rules"] = [
        rule for rule in document["field_rules"] if rule["field_path"] != "/payload/book_id"
    ]

    with pytest.raises(ValidationError):
        _validate_json_schema("source-of-truth-policy", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("source-of-truth-policy", document)


@pytest.mark.parametrize(
    ("contract", "filename", "path"),
    [
        ("execution-observation", "execution-spot.json", ("payload", "execution_time")),
        ("trade-capture-observation", "trade-capture-forward.json", ("payload", "capture_time")),
        ("confirmation-observation", "confirmation-spot.json", ("payload", "confirmation_time")),
        ("booking-observation", "booking-forward.json", ("payload", "last_updated_time")),
        ("canonical-trade", "canonical-trade-spot.json", ("created_at",)),
        ("canonical-trade-state", "canonical-trade-state-spot.json", ("source_watermark",)),
        ("linkage-decision", "linkage-accepted.json", ("created_at",)),
        (
            "canonical-trade",
            "canonical-trade-spot.json",
            ("field_provenance", "product_type", "observed_at"),
        ),
    ],
)
def test_all_material_timestamps_require_timezone(
    contract: str, filename: str, path: tuple[str, ...]
) -> None:
    document = _load_json(EXAMPLES / "valid" / filename)
    target: Any = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "2026-07-31T09:30:00"

    with pytest.raises(PydanticValidationError):
        validate_contract_document(contract, document)


def test_mixed_timezone_awareness_is_a_validation_error() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-spot.json")
    document["created_at"] = "2026-07-31T09:30:00"

    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade", document)


def test_observation_revisions_use_new_identity_not_entity_version_mutation() -> None:
    document = _load_json(EXAMPLES / "valid" / "execution-spot.json")
    document["entity_version"] = 2

    with pytest.raises(PydanticValidationError):
        validate_contract_document("execution-observation", document)


def test_observation_payload_time_must_be_available_at_ingest() -> None:
    document = _load_json(EXAMPLES / "valid" / "execution-spot.json")
    document["payload"]["execution_time"] = "2030-01-01T00:00:00Z"

    with pytest.raises(PydanticValidationError):
        validate_contract_document("execution-observation", document)


def test_canonical_state_requires_lineage_and_watermark_cutoffs() -> None:
    future_watermark = _load_json(EXAMPLES / "valid" / "canonical-trade-state-spot.json")
    future_watermark["source_watermark"] = "2026-07-31T09:31:06Z"
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade-state", future_watermark)

    missing_source = _load_json(EXAMPLES / "valid" / "canonical-trade-state-spot.json")
    missing_source["field_provenance"]["product_type"]["source_observation_id"] = "obs_missing"
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade-state", missing_source)

    late_provenance = _load_json(EXAMPLES / "valid" / "canonical-trade-state-spot.json")
    late_provenance["field_provenance"]["product_type"]["ingested_at"] = "2026-07-31T09:31:05Z"
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade-state", late_provenance)


def test_source_family_identity_excludes_revision_version() -> None:
    document = _load_json(EXAMPLES / "valid" / "identity-policy.json")
    policy = validate_contract_document("identity-policy", document)

    assert policy.source_identity_fields == [
        "tenant_id",
        "portfolio_id",
        "source_system",
        "observation_kind",
        "source_business_key",
    ]
    assert policy.source_version_field == "source_version"
    conditions = {row.outcome: row.condition for row in policy.outcomes}
    assert conditions["NEW_SOURCE_VERSION"].same_source_identity is True
    assert conditions["NEW_SOURCE_VERSION"].source_version_relation == "GREATER"
    assert conditions["LATE_SOURCE_VERSION_RECORDED"].same_source_identity is True
    assert conditions["LATE_SOURCE_VERSION_RECORDED"].source_version_relation == "LOWER"


def test_product_settlement_invariants_are_discriminated() -> None:
    spot = _load_json(EXAMPLES / "valid" / "execution-spot.json")
    spot["payload"]["value_date"] = "2027-07-31"
    with pytest.raises(PydanticValidationError):
        validate_contract_document("execution-observation", spot)

    forward = _load_json(EXAMPLES / "valid" / "trade-capture-forward.json")
    forward["payload"]["value_date"] = forward["payload"]["trade_date"]
    with pytest.raises(PydanticValidationError):
        validate_contract_document("trade-capture-observation", forward)


def test_operation_status_matrices_reject_contradictory_facts() -> None:
    document = _load_json(EXAMPLES / "valid" / "execution-spot.json")
    document["payload"]["execution_type"] = "CANCEL"
    document["payload"]["execution_status"] = "EXECUTED"

    with pytest.raises(PydanticValidationError):
        validate_contract_document("execution-observation", document)


def test_cross_scope_rejection_requires_a_scope_mismatch_candidate() -> None:
    document = _load_json(EXAMPLES / "valid" / "linkage-accepted.json")
    document["decision"] = "CROSS_SCOPE_REJECTED"
    document["reason_code"] = "TENANT_OR_PORTFOLIO_SCOPE_MISMATCH"
    document["chosen_trade_id"] = None
    document["candidate_links"][0]["portfolio_id"] = "portfolio_sydney"

    validate_contract_document("linkage-decision", document)

    document["candidate_links"] = []
    with pytest.raises(PydanticValidationError):
        validate_contract_document("linkage-decision", document)


def test_source_of_truth_policy_covers_every_canonical_field() -> None:
    document = _load_json(EXAMPLES / "valid" / "source-of-truth-policy.json")
    policy = validate_contract_document("source-of-truth-policy", document)
    assert {rule.field_path for rule in policy.field_rules} >= {
        "/payload/product_type",
        "/payload/settlement_rule_version",
        "/payload/base_currency",
        "/payload/terms_currency",
        "/payload/side",
        "/payload/base_amount",
        "/payload/terms_amount",
        "/payload/quoted_rate",
        "/payload/trade_date",
        "/payload/value_date",
        "/payload/lifecycle_status",
        "/payload/counterparty_id",
        "/payload/book_id",
    }


@pytest.mark.parametrize(
    "outcome",
    [
        "NEW_OBSERVATION",
        "IDEMPOTENT_REPLAY",
        "DUPLICATE_SOURCE_CONFLICT",
        "NEW_SOURCE_VERSION",
        "LATE_SOURCE_VERSION_RECORDED",
        "REJECT_UNSUPPORTED_SCHEMA_VERSION",
        "REJECT_CROSS_PORTFOLIO_LINKAGE",
        "LINKAGE_REVIEW_REQUIRED",
    ],
)
def test_identity_policy_rejects_unconstrained_condition_for_each_outcome(
    outcome: str,
) -> None:
    document = _load_json(EXAMPLES / "valid" / "identity-policy.json")
    row = next(item for item in document["outcomes"] if item["outcome"] == outcome)
    row["condition"] = {"description": "unconstrained"}

    with pytest.raises(ValidationError):
        _validate_json_schema("identity-policy", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("identity-policy", document)


def test_canonical_provenance_must_match_top_level_scope() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-spot.json")
    document["field_provenance"]["product_type"]["source_portfolio_id"] = "portfolio_sydney"

    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade", document)

    state = _load_json(EXAMPLES / "valid" / "canonical-trade-state-spot.json")
    state["source_version_set"][0]["source_portfolio_id"] = "portfolio_sydney"
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade-state", state)


def test_field_provenance_key_must_match_field_path() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-spot.json")
    document["field_provenance"]["product_type"]["field_path"] = "/payload/book_id"

    with pytest.raises(ValidationError):
        _validate_json_schema("canonical-trade", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade", document)


def test_source_version_set_rejects_duplicate_observation_ids() -> None:
    document = _load_json(EXAMPLES / "valid" / "canonical-trade-state-spot.json")
    duplicate = dict(document["source_version_set"][1])
    duplicate["source_version"] = "2"
    duplicate["content_hash"] = (
        "sha256:4444444444444444444444444444444444444444444444444444444444444444"
    )
    document["source_version_set"].append(duplicate)
    document["field_provenance"]["lifecycle_status"]["source_version"] = "2"

    _validate_json_schema("canonical-trade-state", document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("canonical-trade-state", document)


@pytest.mark.parametrize(
    ("contract", "filename", "path"),
    [
        ("execution-observation", "execution-spot.json", ("payload", "settlement_rule_version")),
        ("canonical-trade", "canonical-trade-spot.json", ("state", "settlement_rule_version")),
        (
            "canonical-trade-state",
            "canonical-trade-state-spot.json",
            ("state", "settlement_rule_version"),
        ),
    ],
)
def test_unsupported_settlement_rule_version_fails_closed(
    contract: str, filename: str, path: tuple[str, ...]
) -> None:
    document = _load_json(EXAMPLES / "valid" / filename)
    target: Any = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "9.9.9"

    with pytest.raises(ValidationError):
        _validate_json_schema(contract, document)
    with pytest.raises(PydanticValidationError):
        validate_contract_document(contract, document)
