# packages/persistence

`source_event_inbox` + versioned canonical projection (ADR-008 §2, ADR-001).
Owner: Bumble (persistence/DDL) + Honey (contracts/provenance review). Epic
E4, issue #10 (TS-10).

## Scope

This package supplies the canonical assembly boundary, an in-memory ingest
reference, and PostgreSQL 16 DDL. It does not yet supply the production
transactional psycopg adapter.

- `ddl/0001_canonical_persistence.sql` — canonical PostgreSQL 16 schema:
  `source_event_inbox` and the append-only `canonical_trade_state_versions`.
  Columns mirror `ObservationEnvelope`/`CanonicalTradeState`
  (`packages/contracts/models.py`) field-for-field — including
  `schema_version`, `entity_version`, and `actor`, not just `payload`/
  `state` — so a row can reconstruct the full source envelope, and a
  coupled `CHECK` constraint enforces the same valid
  `observation_kind`/`source_system` pairing the Pydantic observation
  subclasses enforce (no independently-valid-but-impossible pairs).
- `inbox.py` — `InboxStore`, a pure in-memory reference implementation of
  the ingest decision (`INSERTED` / `IDEMPOTENT_REPLAY` /
  `SourceConflictError`). Storage-agnostic on purpose: any real adapter
  (e.g. a psycopg-backed store) must reproduce the same three outcomes
  against the same `source_event_inbox_identity_version_key` constraint.
- `assembler.py` — loads and enforces the packaged versioned
  `SourceOfTruthPolicy`, resolves all 13 canonical fields deterministically,
  and independently checks any supplied `field_selection`. An unauthorised
  substitution raises `SourceOfTruthSelectionError`; an unsupported policy
  version raises `SourceOfTruthPolicyVersionError`. The full locked source
  set remains separate from the selected per-field operands and is retained
  in `source_version_set` for conflict/missing-source reconciliation.
- `hashing.py` in `packages/contracts` — defines the versioned canonical
  observation hash used at ingress. Delivery-only IDs/times are excluded;
  semantic fields, timestamps, decimal strings and JSON key ordering are
  canonicalised before SHA-256 validation.

## Consistency item C-09 — identity vs. delivery

The `source_event_inbox` unique key is the **stable source-family
identity + version** —
`(tenant_id, portfolio_id, source_system, observation_kind,
source_business_key, source_version)` — not **delivery identity**
(`source_system, source_event_id`), which changes on every
retransmission/replay even when the logical revision is unchanged
(ADR-001 / identity-policy). `content_hash` is stored as a separate
column and is never part of the uniqueness constraint:

- Same identity/version + **same** `content_hash` → idempotent replay
  (no-op; the existing row is authoritative).
- Same identity/version + **different** `content_hash` →
  `DUPLICATE_SOURCE_CONFLICT`, raised deterministically and never
  silently applied or duplicated.

The database constraint (`source_event_inbox_identity_version_key`) prevents
two rows for the same identity/version. Database triggers independently
reject `UPDATE` and `DELETE`. The transactional distinction between replay
and conflict in a real psycopg adapter, including concurrent-writer tests,
is intentionally tracked as follow-up hardening; `InboxStore` is the tested
semantic reference, not a claim of production database parity.

A delivery-level index (`source_event_inbox_delivery_idx` on
`source_system, source_event_id`) exists for audit/evidence lookups only
and must never substitute for the identity/version conflict key.

## Verification

`tests/integration/test_postgres_persistence.py` applies the migration to a
fresh PostgreSQL schema, proves inbox `UPDATE`/`DELETE` rejection, re-applies
the migration, and checks portfolio-scoped canonical indexes. CI executes
this suite against PostgreSQL 16. Local runs require an explicitly disposable
`TRADEOPS_TEST_DATABASE_URL`; without it, the integration module skips.
