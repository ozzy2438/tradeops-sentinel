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


def _manifest() -> dict[str, dict[str, str]]:
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


@pytest.mark.parametrize("filename,contract", _manifest()["invalid"].items())
def test_invalid_examples_fail_closed(filename: str, contract: str) -> None:
    document = _load_json(EXAMPLES / "invalid" / filename)
    schema_rejected = False
    pydantic_rejected = False

    try:
        _validate_json_schema(contract, document)
    except ValidationError:
        schema_rejected = True

    try:
        validate_contract_document(contract, document)
    except PydanticValidationError:
        pydantic_rejected = True

    assert schema_rejected or pydantic_rejected


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
