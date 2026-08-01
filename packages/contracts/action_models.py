"""TS-5 non-executable signed-action and evidence contracts.

This module defines the payload that a future action compiler and signing
boundary will exchange.  It deliberately contains no signing, verification,
dispatch, persistence, or executor behaviour.  The only computation exposed
here is deterministic canonical encoding and hashing of an instruction draft.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, StringConstraints, model_validator

from .models import (
    Actor,
    AwareTimestamp,
    ContractModel,
    CorrelationId,
    Identifier,
    PortfolioId,
    SchemaVersion,
    Sha256,
    SourceVersion,
    TenantId,
)

ActionType: TypeAlias = Literal["SET_CONFIRMATION_REFERENCE"]
FinalSubmitControlType: TypeAlias = Literal["CAS", "LEASE"]
SignatureAlgorithm: TypeAlias = Literal["ED25519"]
EvidenceClassification: TypeAlias = Literal[
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
]
EvidenceRedactionStatus: TypeAlias = Literal[
    "NOT_REQUIRED",
    "RESTRICTED_ORIGINAL",
    "REDACTED_DERIVATIVE",
    "REDACTION_FAILED",
]
EvidenceRetentionClass: TypeAlias = Literal["STANDARD", "RESTRICTED", "SHORT_LIVED"]
EvidenceId = Annotated[
    str,
    StringConstraints(pattern=r"^evidence_[a-z0-9][a-z0-9_-]*$", min_length=3, max_length=128),
]
ArtifactId = Annotated[
    str,
    StringConstraints(pattern=r"^artifact_[a-z0-9][a-z0-9_-]*$", min_length=3, max_length=128),
]
EvidenceKind: TypeAlias = Literal[
    "SOURCE_OBSERVATION_HASH",
    "NORMALISED_OBSERVATION",
    "CANONICAL_STATE",
    "RECONCILIATION_RESULT",
    "RULE_VERSION",
    "MODEL_VERSION",
    "PROMPT_VERSION",
    "CORPUS_VERSION",
    "POLICY_VERSION",
    "TOOL_CALL",
    "AGENT_OUTPUT",
    "CITATION_VALIDATION",
    "REVIEW_REQUEST",
    "MAKER_DECISION",
    "CHECKER_DECISION",
    "ELIGIBILITY_DECISION",
    "ACTION_DRAFT",
    "SIGNED_INSTRUCTION",
    "DISPATCH_ATTEMPT",
    "EXECUTOR_RECEIPT",
    "PRE_ACTION_READ",
    "POST_ACTION_READ",
    "CHANGED_FIELD_DIFF",
    "AUDIT_EVENT",
    "RELEASE_BUILD",
    "CONFIGURATION_TUPLE",
    "EVIDENCE_MANIFEST",
]

IdempotencyKey = Annotated[
    str,
    StringConstraints(pattern=r"^idem:[0-9a-f]{64}$"),
]
SingleUseNonce = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$"),
]
Base64Signature = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9+/]+={0,2}$", min_length=16, max_length=4096),
]
MediaType = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", max_length=128),
]


class VersionReference(ContractModel):
    """A version-pinned reference consumed by an instruction or evidence item."""

    reference_id: Identifier
    version: int = Field(ge=1)


class SourceObservationVersionReference(ContractModel):
    observation_id: Identifier
    source_version: SourceVersion
    content_hash: Sha256


class EvidenceManifestReference(ContractModel):
    manifest_id: Identifier
    manifest_version: int = Field(ge=1)
    content_hash: Sha256


class FinalSubmitControl(ContractModel):
    """Opaque CAS/LEASE reference; the gateway owns its interpretation."""

    control_type: FinalSubmitControlType
    control_reference: Identifier
    lease_expires_at: AwareTimestamp | None

    @model_validator(mode="after")
    def validate_control_reference(self) -> FinalSubmitControl:
        if self.control_type == "CAS" and self.lease_expires_at is not None:
            raise ValueError("CAS controls must not carry a lease expiry")
        if self.control_type == "LEASE" and self.lease_expires_at is None:
            raise ValueError("LEASE controls require lease_expires_at")
        return self


_HASH_EXCLUDED_FIELDS = frozenset(
    {
        "content_hash",
        "idempotency_key",
        "signer_key_id",
        "signature_algorithm",
        "signature",
    }
)


def _json_mapping(document: Mapping[str, Any] | ContractModel) -> dict[str, Any]:
    if isinstance(document, ContractModel):
        return document.model_dump(mode="json", exclude_none=False)
    return dict(document)


def _canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _normalise_json_value(document),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _normalise_json_value(value: Any) -> Any:
    """Normalise JSON-compatible values used by canonical encoding v1."""

    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {key: _normalise_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_json_value(item) for item in value]
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return parsed.isoformat().replace("+00:00", "Z")
    return value


def canonical_action_payload(
    document: Mapping[str, Any] | ContractModel,
) -> bytes:
    """Return the version-1 canonical bytes for the locked instruction draft.

    The content hash and idempotency key are derived fields and therefore are
    excluded.  Signature metadata is also excluded so adding a future
    signature cannot change the draft hash that maker/checker approvals bind.
    """

    values = _json_mapping(document)
    locked_values = {
        key: value for key, value in values.items() if key not in _HASH_EXCLUDED_FIELDS
    }
    return _canonical_json_bytes(locked_values)


def compute_action_content_hash(
    document: Mapping[str, Any] | ContractModel,
) -> str:
    """Compute the deterministic SHA-256 content hash for an instruction draft."""

    return f"sha256:{sha256(canonical_action_payload(document)).hexdigest()}"


def compute_idempotency_key(
    document: Mapping[str, Any] | ContractModel,
) -> str:
    """Compute the ADR-005 semantic idempotency key.

    The key binds exactly the tenant, portfolio, trade, action type, target
    booking version, normalised old value, exact new value, and draft hash.
    It expresses at-most-once semantic identity; it does not claim exactly-once
    transport or execution.
    """

    values = _json_mapping(document)
    required = (
        "tenant_id",
        "portfolio_id",
        "trade_id",
        "action_type",
        "target_booking_version",
        "normalised_expected_old_value",
        "exact_approved_new_value",
        "content_hash",
    )
    missing = [field_name for field_name in required if field_name not in values]
    if missing:
        raise ValueError(f"idempotency material is missing fields: {missing}")
    material = {field_name: values[field_name] for field_name in required}
    return f"idem:{sha256(_canonical_json_bytes(material)).hexdigest()}"


class SignedActionInstruction(ContractModel):
    """Versioned non-executable action instruction draft/envelope.

    Signature fields are typed as an all-or-none envelope, but are not created
    or cryptographically verified in TS-5.  An unsigned draft is the expected
    MVP fixture shape.
    """

    instruction_id: Identifier
    instruction_schema_version: SchemaVersion
    action_type: ActionType
    tenant_id: TenantId
    portfolio_id: PortfolioId
    case_id: Identifier
    trade_id: Identifier
    target_booking_id: Identifier
    canonical_state_version: VersionReference
    source_observation_versions: list[SourceObservationVersionReference] = Field(min_length=1)
    reconciliation_version: VersionReference
    recommendation_version: VersionReference
    policy_version: VersionReference
    maker_decision_version: VersionReference
    checker_decision_version: VersionReference
    target_field_path: Literal["/payload/confirmation_reference"]
    normalised_expected_old_value: str | None = Field(max_length=128)
    exact_approved_new_value: str = Field(min_length=1, max_length=128)
    target_booking_version: int = Field(ge=1)
    final_submit_control: FinalSubmitControl
    issued_at: AwareTimestamp
    not_before: AwareTimestamp
    expires_at: AwareTimestamp
    single_use_nonce: SingleUseNonce
    idempotency_key: IdempotencyKey
    content_hash: Sha256
    signer_key_id: Identifier | None = None
    signature_algorithm: SignatureAlgorithm | None = None
    signature: Base64Signature | None = None
    cancellation_revocation_lookup_reference: VersionReference
    evidence_manifest_reference: EvidenceManifestReference

    @model_validator(mode="after")
    def validate_instruction_invariants(self) -> SignedActionInstruction:
        if (
            self.normalised_expected_old_value is not None
            and self.normalised_expected_old_value == self.exact_approved_new_value
        ):
            raise ValueError("approved new value must differ from the expected old value")
        if self.not_before < self.issued_at:
            raise ValueError("not_before must not precede issued_at")
        if self.expires_at <= self.not_before:
            raise ValueError("expires_at must be after not_before")
        lease_expiry = self.final_submit_control.lease_expires_at
        if lease_expiry is not None and (
            lease_expiry <= self.not_before or lease_expiry > self.expires_at
        ):
            raise ValueError("lease expiry must be after not_before and no later than expires_at")

        source_ids = [item.observation_id for item in self.source_observation_versions]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_observation_versions observation IDs must be unique")
        if self.maker_decision_version.reference_id == self.checker_decision_version.reference_id:
            raise ValueError("maker and checker decision references must be distinct")

        signature_fields = (
            self.signer_key_id,
            self.signature_algorithm,
            self.signature,
        )
        if any(field is None for field in signature_fields) and any(
            field is not None for field in signature_fields
        ):
            raise ValueError("signer_key_id, signature_algorithm, and signature are all-or-none")

        expected_content_hash = compute_action_content_hash(self)
        if self.content_hash != expected_content_hash:
            raise ValueError("content_hash does not match the canonical locked instruction payload")
        expected_idempotency_key = compute_idempotency_key(self)
        if self.idempotency_key != expected_idempotency_key:
            raise ValueError("idempotency_key does not match the ADR-005 composition")
        return self


class EvidenceReference(ContractModel):
    reference_id: Identifier
    reference_version: int = Field(ge=1)
    source_version: SourceVersion | None = None
    content_hash: Sha256 | None = None


class EvidenceItem(ContractModel):
    """Versioned, scoped evidence record referencing an ADR-012 artefact.

    ``evidence_id`` identifies the immutable evidence-record lineage. A
    revised record receives a new evidence ID and points to its predecessor
    through ``supersedes_evidence_id``; ``evidence_id`` is therefore the
    natural evidence-record key.

    ``artifact_id`` is a separate stable logical identity for the stored
    artefact. ``artifact_version`` identifies an immutable version under that
    identity, so the artifact persistence key is the pair
    ``(artifact_id, artifact_version)``. Multiple evidence records may refer
    to the same artifact pair within the same tenant/portfolio/case scope.
    """

    evidence_id: EvidenceId
    evidence_schema_version: SchemaVersion
    evidence_version: int = Field(ge=1)
    artifact_id: ArtifactId
    artifact_version: int = Field(
        ge=1,
        description="Immutable artifact version under the stable artifact_id.",
    )
    tenant_id: TenantId
    portfolio_id: PortfolioId
    case_id: Identifier
    correlation_id: CorrelationId
    evidence_kind: EvidenceKind
    source_reference: EvidenceReference
    content_hash: Sha256
    media_type: MediaType
    classification: EvidenceClassification
    redaction_status: EvidenceRedactionStatus
    producer: Actor
    retention_class: EvidenceRetentionClass
    captured_at: AwareTimestamp
    created_at: AwareTimestamp
    supersedes_evidence_id: EvidenceId | None = None
    derivative_of_evidence_id: EvidenceId | None = None

    @model_validator(mode="after")
    def validate_evidence_invariants(self) -> EvidenceItem:
        if self.created_at < self.captured_at:
            raise ValueError("created_at must not precede captured_at")
        if self.evidence_version == 1 and self.supersedes_evidence_id is not None:
            raise ValueError("evidence version one must not supersede another item")
        if self.evidence_version > 1 and self.supersedes_evidence_id is None:
            raise ValueError("evidence revisions must reference the superseded evidence item")
        if self.supersedes_evidence_id == self.evidence_id:
            raise ValueError("evidence must not supersede itself")
        if self.redaction_status == "REDACTED_DERIVATIVE":
            if self.derivative_of_evidence_id is None:
                raise ValueError("redacted derivatives require derivative_of_evidence_id")
            if self.derivative_of_evidence_id == self.evidence_id:
                raise ValueError("evidence must not derive from itself")
        elif self.derivative_of_evidence_id is not None:
            raise ValueError("only redacted derivatives may carry derivative_of_evidence_id")
        if self.evidence_kind == "SOURCE_OBSERVATION_HASH":
            if (
                self.source_reference.source_version is None
                or self.source_reference.content_hash is None
            ):
                raise ValueError(
                    "source observation evidence requires source_version and source content_hash"
                )
        return self


__all__ = [
    "ActionType",
    "ArtifactId",
    "EvidenceId",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceManifestReference",
    "EvidenceReference",
    "FinalSubmitControl",
    "SignedActionInstruction",
    "SourceObservationVersionReference",
    "VersionReference",
    "canonical_action_payload",
    "compute_action_content_hash",
    "compute_idempotency_key",
]
