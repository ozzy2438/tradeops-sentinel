"""source_event_inbox ingest decision logic (issue #10 / consistency item C-09).

This module is deliberately storage-agnostic: :class:`InboxStore` is a pure
in-memory reference implementation of the ingest outcomes.  PostgreSQL
enforces uniqueness and append-only storage through
``ddl/0001_canonical_persistence.sql``; a transactional psycopg adapter and
its concurrency semantics remain a separate hardening slice.

Conflict key (Honey, 2026-08-02T23:34 — ADR-001 / identity-policy):
source identity/version is the *stable source-family* identity —
``(tenant_id, portfolio_id, source_system, observation_kind,
source_business_key, source_version)`` — not delivery identity
(``source_system, source_event_id``), which changes on every
retransmission/replay even for the same logical revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from packages.contracts.hashing import validate_observation_content_hash
from packages.contracts.models import DuplicateSourceConflict, ObservationEnvelope


class IdentityKey(NamedTuple):
    """The C-09 source identity/version conflict key."""

    tenant_id: str
    portfolio_id: str
    source_system: str
    observation_kind: str
    source_business_key: str
    source_version: str


def identity_key(observation: ObservationEnvelope) -> IdentityKey:
    return IdentityKey(
        tenant_id=observation.tenant_id,
        portfolio_id=observation.portfolio_id,
        source_system=observation.source_system,
        observation_kind=observation.observation_kind,
        source_business_key=observation.source_business_key,
        source_version=observation.source_version,
    )


class IngestOutcome(StrEnum):
    INSERTED = "INSERTED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


@dataclass(frozen=True)
class InboxRecord:
    """The row that survives an ingest — either newly inserted or the
    pre-existing row returned as-is for an idempotent replay."""

    observation: ObservationEnvelope
    identity_key: IdentityKey


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    record: InboxRecord


class SourceConflictError(Exception):
    """Raised when the same source identity/version arrives with a
    different content_hash (C-09: DUPLICATE_SOURCE_CONFLICT)."""

    def __init__(self, conflict: DuplicateSourceConflict) -> None:
        self.conflict = conflict
        super().__init__(
            f"DUPLICATE_SOURCE_CONFLICT: source_business_key="
            f"{conflict.source_business_key!r} source_version="
            f"{conflict.source_version!r} observation_ids="
            f"{conflict.source_observation_ids!r}"
        )


class InboxStore:
    """Pure in-memory reference implementation of source_event_inbox.

    Enforces exactly the ``source_event_inbox_identity_version_key``
    constraint from the DDL: unique on the C-09 identity/version key, with
    content_hash compared separately to distinguish idempotent replay from
    conflict. Never mutates or removes an existing row.
    """

    def __init__(self) -> None:
        self._by_identity: dict[IdentityKey, InboxRecord] = {}

    def ingest(self, observation: ObservationEnvelope) -> IngestResult:
        # Never trust a caller-supplied fingerprint.  Validation happens before
        # either insertion or replay/conflict classification, so a forged hash
        # cannot suppress a material payload difference.
        validate_observation_content_hash(observation)
        key = identity_key(observation)
        existing = self._by_identity.get(key)

        if existing is None:
            record = InboxRecord(observation=observation, identity_key=key)
            self._by_identity[key] = record
            return IngestResult(outcome=IngestOutcome.INSERTED, record=record)

        if existing.observation.content_hash == observation.content_hash:
            # Same identity/version, same content: idempotent replay.
            # The pre-existing row is authoritative; nothing is written.
            return IngestResult(outcome=IngestOutcome.IDEMPOTENT_REPLAY, record=existing)

        # Same identity/version, different content: deterministic conflict.
        # Never silently overwritten or duplicated (C-09).
        conflict = DuplicateSourceConflict(
            conflict_type="SAME_SOURCE_KEY_VERSION_CONTENT",
            source_observation_ids=sorted(
                {existing.observation.observation_id, observation.observation_id}
            ),
            source_business_key=key.source_business_key,
            source_version=key.source_version,
        )
        raise SourceConflictError(conflict)

    def get(self, key: IdentityKey) -> InboxRecord | None:
        return self._by_identity.get(key)

    def all_records(self) -> tuple[InboxRecord, ...]:
        return tuple(self._by_identity.values())
