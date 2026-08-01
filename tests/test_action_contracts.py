"""TS-5 contract, schema, and deterministic hash tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource

from packages.contracts.action_models import (
    compute_action_content_hash,
    compute_idempotency_key,
)
from packages.contracts.models import validate_contract_document

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "packages" / "contracts"
SCHEMAS = CONTRACTS / "schemas"
EXAMPLES = CONTRACTS / "examples" / "valid"


def _load(filename: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        resources.append((path.name, Resource.from_contents(json.loads(path.read_text()))))
    return Registry().with_resources(resources)


def _validate_schema(contract: str, document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS / f"{contract}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=FormatChecker(),
    ).validate(document)


def test_ts5_examples_pass_schema_and_pydantic_layers() -> None:
    action = _load("action-instruction.json")
    evidence = _load("evidence-item.json")

    _validate_schema("action-instruction", action)
    _validate_schema("evidence-item", evidence)
    validate_contract_document("action-instruction", action)
    validate_contract_document("evidence-item", evidence)


def test_content_hash_is_deterministic_and_field_changes_move_hash() -> None:
    document = _load("action-instruction.json")
    first = compute_action_content_hash(document)
    second = compute_action_content_hash(deepcopy(document))
    assert first == second == document["content_hash"]

    changed = deepcopy(document)
    changed["exact_approved_new_value"] = "CONF-2026-002"
    changed_hash = compute_action_content_hash(changed)
    assert changed_hash != first
    changed["content_hash"] = changed_hash
    changed["idempotency_key"] = compute_idempotency_key(changed)

    _validate_schema("action-instruction", changed)
    validate_contract_document("action-instruction", changed)


def test_exact_approved_values_are_byte_exact_in_hash_and_idempotency() -> None:
    base = _load("action-instruction.json")
    first = deepcopy(base)
    first["exact_approved_new_value"] = "2026-08-01T07:00:00Z"
    first["content_hash"] = compute_action_content_hash(first)
    first["idempotency_key"] = compute_idempotency_key(first)

    second = deepcopy(base)
    second["exact_approved_new_value"] = "2026-08-01T07:00:00+00:00"
    second["content_hash"] = compute_action_content_hash(second)
    second["idempotency_key"] = compute_idempotency_key(second)

    assert first["content_hash"] != second["content_hash"]
    assert first["idempotency_key"] != second["idempotency_key"]
    _validate_schema("action-instruction", first)
    _validate_schema("action-instruction", second)
    validate_contract_document("action-instruction", first)
    validate_contract_document("action-instruction", second)


def test_timestamp_mapping_offsets_match_typed_canonical_form() -> None:
    representations = (
        (
            "2026-08-01T07:00:00Z",
            "2026-08-01T07:00:30Z",
            "2026-08-01T08:00:00Z",
            "2026-08-01T07:30:00Z",
        ),
        (
            "2026-08-01T07:00:00+00:00",
            "2026-08-01T07:00:30+00:00",
            "2026-08-01T08:00:00+00:00",
            "2026-08-01T07:30:00+00:00",
        ),
        (
            "2026-08-01T17:00:00+10:00",
            "2026-08-01T17:00:30+10:00",
            "2026-08-01T18:00:00+10:00",
            "2026-08-01T17:30:00+10:00",
        ),
    )
    hashes: list[str] = []
    idempotency_keys: list[str] = []

    for issued_at, not_before, expires_at, lease_expires_at in representations:
        document = deepcopy(_load("action-instruction.json"))
        document["issued_at"] = issued_at
        document["not_before"] = not_before
        document["expires_at"] = expires_at
        document["final_submit_control"] = {
            "control_type": "LEASE",
            "control_reference": "lease_001",
            "lease_expires_at": lease_expires_at,
        }
        document["content_hash"] = compute_action_content_hash(document)
        document["idempotency_key"] = compute_idempotency_key(document)

        _validate_schema("action-instruction", document)
        model = validate_contract_document("action-instruction", document)
        assert compute_action_content_hash(model) == document["content_hash"]
        assert compute_idempotency_key(model) == document["idempotency_key"]
        hashes.append(document["content_hash"])
        idempotency_keys.append(document["idempotency_key"])

    assert len(set(hashes)) == 1
    assert len(set(idempotency_keys)) == 1


def test_action_references_must_match_instruction_scope() -> None:
    source_scope_mismatch = deepcopy(_load("action-instruction.json"))
    source_scope_mismatch["source_observation_versions"][0]["scope"]["tenant_id"] = "tenant_other"
    source_scope_mismatch["content_hash"] = compute_action_content_hash(source_scope_mismatch)
    source_scope_mismatch["idempotency_key"] = compute_idempotency_key(source_scope_mismatch)
    _validate_schema("action-instruction", source_scope_mismatch)
    with pytest.raises(PydanticValidationError, match="source observations"):
        validate_contract_document("action-instruction", source_scope_mismatch)

    manifest_scope_mismatch = deepcopy(_load("action-instruction.json"))
    manifest_scope_mismatch["evidence_manifest_reference"]["scope"]["case_id"] = "case_other"
    manifest_scope_mismatch["content_hash"] = compute_action_content_hash(manifest_scope_mismatch)
    manifest_scope_mismatch["idempotency_key"] = compute_idempotency_key(manifest_scope_mismatch)
    _validate_schema("action-instruction", manifest_scope_mismatch)
    with pytest.raises(PydanticValidationError, match="evidence manifest"):
        validate_contract_document("action-instruction", manifest_scope_mismatch)


def test_source_observation_order_is_stable_for_canonical_identity() -> None:
    document = deepcopy(_load("action-instruction.json"))
    second_source = deepcopy(document["source_observation_versions"][0])
    second_source["observation_id"] = "obs_confirmation_002"
    second_source["content_hash"] = (
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    document["source_observation_versions"].append(second_source)
    document["content_hash"] = compute_action_content_hash(document)
    document["idempotency_key"] = compute_idempotency_key(document)

    reversed_document = deepcopy(document)
    reversed_document["source_observation_versions"] = list(
        reversed(reversed_document["source_observation_versions"])
    )

    assert compute_action_content_hash(reversed_document) == document["content_hash"]
    assert compute_idempotency_key(reversed_document) == document["idempotency_key"]
    reversed_document["content_hash"] = compute_action_content_hash(reversed_document)
    reversed_document["idempotency_key"] = compute_idempotency_key(reversed_document)
    _validate_schema("action-instruction", reversed_document)
    validate_contract_document("action-instruction", reversed_document)


def test_idempotency_key_binds_the_locked_material() -> None:
    document = _load("action-instruction.json")
    changed = deepcopy(document)
    changed["trade_id"] = "trade_fx_002"
    changed["source_observation_versions"][0]["scope"]["trade_id"] = changed["trade_id"]
    changed["evidence_manifest_reference"]["scope"]["trade_id"] = changed["trade_id"]

    _validate_schema("action-instruction", changed)
    with pytest.raises(PydanticValidationError, match="content_hash"):
        validate_contract_document("action-instruction", changed)

    changed["content_hash"] = compute_action_content_hash(changed)
    _validate_schema("action-instruction", changed)
    with pytest.raises(PydanticValidationError, match="idempotency_key"):
        validate_contract_document("action-instruction", changed)


def test_signature_fields_are_all_or_none_without_verification_code() -> None:
    document = deepcopy(_load("action-instruction.json"))
    document["signer_key_id"] = "signer_key_001"

    with pytest.raises(ValidationError):
        _validate_schema("action-instruction", document)
    with pytest.raises(PydanticValidationError, match="all-or-none"):
        validate_contract_document("action-instruction", document)


def test_lease_requires_a_typed_expiry_reference() -> None:
    document = deepcopy(_load("action-instruction.json"))
    document["final_submit_control"] = {
        "control_type": "LEASE",
        "control_reference": "lease_001",
        "lease_expires_at": None,
    }

    with pytest.raises(ValidationError):
        _validate_schema("action-instruction", document)
    with pytest.raises(PydanticValidationError, match="LEASE controls require"):
        validate_contract_document("action-instruction", document)


def test_source_observation_evidence_requires_source_version_and_hash() -> None:
    document = deepcopy(_load("evidence-item.json"))
    document["source_reference"].pop("source_version")
    document["source_reference"].pop("content_hash")

    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", document)
    with pytest.raises(PydanticValidationError, match="source observation evidence"):
        validate_contract_document("evidence-item", document)


def test_source_evidence_scope_and_kind_are_bound_to_the_evidence_item() -> None:
    scope_mismatch = deepcopy(_load("evidence-item.json"))
    scope_mismatch["source_reference"]["scope"]["portfolio_id"] = "portfolio_other"
    _validate_schema("evidence-item", scope_mismatch)
    with pytest.raises(PydanticValidationError, match="evidence source reference"):
        validate_contract_document("evidence-item", scope_mismatch)

    missing_source_metadata = deepcopy(_load("evidence-item.json"))
    missing_source_metadata["source_reference"].pop("observation_kind")
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", missing_source_metadata)
    with pytest.raises(PydanticValidationError, match="all-or-none"):
        validate_contract_document("evidence-item", missing_source_metadata)

    wrong_source_system = deepcopy(_load("evidence-item.json"))
    wrong_source_system["source_reference"]["source_system"] = "FIX_EXECUTION"
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", wrong_source_system)
    with pytest.raises(PydanticValidationError, match="source_system"):
        validate_contract_document("evidence-item", wrong_source_system)

    wrong_source_id = deepcopy(_load("evidence-item.json"))
    wrong_source_id["source_reference"]["reference_id"] = "policy_001"
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", wrong_source_id)
    with pytest.raises(PydanticValidationError, match="source reference ID"):
        validate_contract_document("evidence-item", wrong_source_id)


def test_evidence_revision_and_redacted_derivative_links_fail_closed() -> None:
    revision = deepcopy(_load("evidence-item.json"))
    revision["evidence_version"] = 2
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", revision)
    with pytest.raises(PydanticValidationError, match="superseded"):
        validate_contract_document("evidence-item", revision)

    derivative = deepcopy(_load("evidence-item.json"))
    derivative["redaction_status"] = "REDACTED_DERIVATIVE"
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", derivative)
    with pytest.raises(PydanticValidationError, match="derivative_of_evidence_id"):
        validate_contract_document("evidence-item", derivative)


def test_evidence_and_artifact_identity_versions_have_distinct_lineage() -> None:
    document = _load("evidence-item.json")
    replacement = deepcopy(document)
    replacement["evidence_id"] = "evidence_002"
    replacement["evidence_version"] = 2
    replacement["supersedes_evidence_id"] = document["evidence_id"]
    replacement["artifact_version"] = 2
    replacement["content_hash"] = (
        "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    )

    _validate_schema("evidence-item", document)
    _validate_schema("evidence-item", replacement)
    validate_contract_document("evidence-item", document)
    validate_contract_document("evidence-item", replacement)

    shared_artifact = deepcopy(document)
    shared_artifact["evidence_id"] = "evidence_003"
    _validate_schema("evidence-item", shared_artifact)
    validate_contract_document("evidence-item", shared_artifact)

    conflated_namespaces = deepcopy(document)
    conflated_namespaces["evidence_id"] = conflated_namespaces["artifact_id"]
    with pytest.raises(ValidationError):
        _validate_schema("evidence-item", conflated_namespaces)
    with pytest.raises(PydanticValidationError):
        validate_contract_document("evidence-item", conflated_namespaces)


def test_evidence_temporal_and_self_link_invariants_are_semantic_checks() -> None:
    late_created = deepcopy(_load("evidence-item.json"))
    late_created["created_at"] = "2026-08-01T06:59:59Z"
    _validate_schema("evidence-item", late_created)
    with pytest.raises(PydanticValidationError, match="created_at"):
        validate_contract_document("evidence-item", late_created)

    self_link = deepcopy(_load("evidence-item.json"))
    self_link["redaction_status"] = "REDACTED_DERIVATIVE"
    self_link["derivative_of_evidence_id"] = self_link["evidence_id"]
    _validate_schema("evidence-item", self_link)
    with pytest.raises(PydanticValidationError, match="derive from itself"):
        validate_contract_document("evidence-item", self_link)
