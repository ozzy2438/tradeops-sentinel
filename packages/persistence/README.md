# packages/persistence

`source_event_inbox` + versioned canonical projection (ADR-008 §2, ADR-001).
Owner: Bumble (persistence/DDL) + Honey (contracts/provenance review). Epic
E4, issue #10 (TS-10).

## Scope

Persistence and inbox only — no reconciliation logic (TS-11), no generator
changes, no contract changes. `packages/contracts` is consumed as-is.

- `ddl/0001_canonical_persistence.sql` — canonical PostgreSQL 16 schema:
  `source_event_inbox` and the append-only `canonical_trade_state_versions`.
- `inbox.py` — `InboxStore`, a pure in-memory reference implementation of
  the ingest decision (`INSERTED` / `IDEMPOTENT_REPLAY` /
  `SourceConflictError`). Storage-agnostic on purpose: any real adapter
  (e.g. a psycopg-backed store) must reproduce the same three outcomes
  against the same `source_event_inbox_identity_version_key` constraint.
- `assembler.py` — `assemble_canonical_state`, builds one
  `CanonicalTradeState` version from a single accepted observation, with
  full field-level provenance. Does not compare or rank multiple sources
  against each other (that is reconciliation / TS-11); every canonical
  field already arrives populated on each observation's payload, so one
  accepted observation is sufficient to build a complete, valid
  `CanonicalFields` projection. The caller owns choosing the next
  `canonical_state_version` and appending — never updating — a row.

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

The database constraint (`source_event_inbox_identity_version_key`)
provides the fail-closed, race-safe guarantee that no two rows can ever
exist for the same identity/version; the two outcomes above are
distinguished by the application layer comparing `content_hash` before
(or on constraint violation of) the insert — see `inbox.py::InboxStore.ingest`
for the reference logic, and `tests/test_persistence.py` for the required
replay/late-arrival/duplicate-vs-conflict tests plus the append-only
no-destructive-overwrite check.

A delivery-level index (`source_event_inbox_delivery_idx` on
`source_system, source_event_id`) exists for audit/evidence lookups only
and must never substitute for the identity/version conflict key.

## Local verification

The DDL was applied and constraint-tested against a real local
PostgreSQL 14 instance (`pg_ctl`/`psql`) as part of this change: both
unique constraints reject the expected violation, and multiple
`canonical_state_version` rows coexist per `trade_id` with no destructive
overwrite. This is local verification evidence, not a CI service — CI
runs the storage-agnostic Python test suite (`tests/test_persistence.py`)
only, per the existing `pytest -q` gate; no new required CI service was
added.
