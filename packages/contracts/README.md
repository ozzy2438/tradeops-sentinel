# packages/contracts

TS-3 publishes the canonical FX contract consumed by later generator,
persistence, and reconciliation work. TS-4 adds the deterministic trade-break
taxonomy and lifecycle contracts consumed by later inbox/outbox and
reconciliation work. TS-5 adds non-executable evidence and signed-action
instruction contracts plus deterministic draft hashing. This package contains
no database, transport, reconciliation, API, LLM, signing, verification,
dispatch, or executor implementation.

## Contract layers

- `schemas/*.schema.json` are Draft 2020-12 interchange schemas.
- `models.py` contains strict Pydantic v2 models for the same TS-3 and TS-4
  documents and cross-field validation that JSON Schema cannot express by
  itself.
- `examples/` contains valid Spot/Forward observations, canonical records, and
  deterministic trade-break examples, plus intentionally invalid fixtures for
  the negative contract tests.
- `tests/test_contracts.py` validates every example against both layers and
  exercises replay, provenance, scope, version, and temporal semantics.
- `tests/test_break_contracts.py` exercises the exact taxonomy, lifecycle
  transition matrix, deterministic priority key, and TS-4 semantic invariants.
- `tests/test_action_contracts.py` validates the TS-5 instruction/evidence
  schemas, idempotency binding, CAS/LEASE shape, evidence lineage, and
  deterministic content-hash behaviour.

## TS-5 action and evidence contracts

`action-instruction.schema.json` and `evidence-item.schema.json` implement the
non-executable contract surface for [ADR-005](../../docs/adr/ADR-005_SIGNED_ACTION_INSTRUCTION_AND_VERIFICATION_CONTRACT.md)
and [ADR-012](../../docs/adr/ADR-012_TAMPER_EVIDENT_AUDIT_AND_EVIDENCE_POLICY.md).
The only permitted action type is the MVP's
`SET_CONFIRMATION_REFERENCE` update. The instruction binds tenant, portfolio,
trade, booking, consumed versions, normalised old/new values, target booking
version, CAS/LEASE control reference, validity window, nonce, idempotency key,
content hash, revocation lookup, and evidence-manifest reference.

The canonical encoding is version 1 JSON with sorted keys, compact separators,
UTF-8, and typed datetime values rendered in UTC with `Z`. Arbitrary string
values, including `exact_approved_new_value` and
`normalised_expected_old_value`, remain byte-exact; the encoder never guesses
that a string containing `T` is a timestamp. The unordered
`source_observation_versions` set is sorted by the stable tuple
`(scope.tenant_id, scope.portfolio_id, scope.case_id, scope.trade_id,
observation_id, source_version, content_hash, observation_kind, source_system)`
before hashing. Derived `content_hash`,
`idempotency_key`, and future signature metadata are excluded from the locked
draft bytes. The idempotency key is structurally derived from the ADR-005
tenant/portfolio/trade/action/booking-version/old-value/new-value/content-hash
tuple. The package does not create or verify signatures, dispatch instructions,
execute writes, or claim exactly-once delivery.

Evidence items are scoped by tenant, portfolio, case, trade, and correlation ID;
versioned and content-addressed; linked to a producer and source reference;
and explicit about classification, retention, and redaction/derivative state.
Source-observation and evidence-manifest references carry a typed
`ReferenceScope`; Pydantic semantic validation requires each reference scope to
equal its containing action or evidence scope. Source-observation references
also carry the typed observation kind and source system, with their allowed
kind/system pairing and `obs_<kind>_...` identity namespace enforced in both
layers. Generic evidence references may omit source metadata, but
`SOURCE_OBSERVATION_HASH` requires the complete typed source scope, kind,
system, source version, and hash.
The identity pairs are intentionally separate: `evidence_id` plus
`evidence_version` identify an immutable evidence-record lineage, while
`artifact_id` plus `artifact_version` identify a separately stored immutable
artifact version. A revised evidence record gets a new evidence ID and an
explicit predecessor link. Multiple evidence records may reference the same
artifact pair within the same tenant/portfolio/case scope; persistence should
use `evidence_id` as the evidence-record key and
`(artifact_id, artifact_version)` as the artifact key/foreign-key target.
The evidence model uses the bounded ADR-012 claim of tamper-evident application
evidence and does not claim WORM, legal hold, absolute immutability, or
non-repudiation.

## TS-4 break contract

The `break-taxonomy` and `trade-break` documents implement
[ADR-002](../../docs/adr/ADR-002_DETERMINISTIC_TRADE_BREAK_TAXONOMY.md) for
[issue #4](https://github.com/ozzy2438/tradeops-sentinel/issues/4).

The MVP has exactly eight break families:

1. `MISSING_REQUIRED_SOURCE`
2. `AMBIGUOUS_OR_UNMATCHED_LINKAGE`
3. `DUPLICATE_SOURCE_CONFLICT`
4. `CURRENCY_PAIR_OR_SIDE_MISMATCH`
5. `ECONOMIC_VALUE_MISMATCH`
6. `TRADE_OR_VALUE_DATE_MISMATCH`
7. `LIFECYCLE_STATUS_MISMATCH`
8. `POST_ACTION_VERIFICATION_FAILURE`

JSON Schema enforces the closed vocabulary, family-specific combinations,
family field/value-type matrix, and permitted lifecycle edges. Each
comparison carries explicit `evidence_ids`; Pydantic binds those IDs to
same-path, family-allowed evidence roles and source observations. Every
non-missing comparison carries expected and observed source observation IDs
plus versions; cross-source families require distinct operands. Pydantic
additionally enforces cross-field scope, source identity, resolution type and
role, resolution-run linkage, evidence chronology, priority, timestamp, and
comparison invariants. Exact-value fields reject decimal tolerances, while
only decimal amounts and rates may carry an approved numeric tolerance.
JSON Schema enforces structural uniqueness; cross-array ID, role, and
timestamp joins are recorded as semantic-layer checks in the fixture manifest.
Unknown taxonomy and contract versions fail closed.

`MISSING_REQUIRED_SOURCE` carries a typed `missing_source_expectation` with
the expected observation kind and source system, field path, arrival-window
rule version, ingestion watermark, and expected-by timestamp. The expected
kind is limited to execution, confirmation, or booking; trade capture is an
observed context, never an expected missing source. Its comparison field is
the typed `/source/{execution,confirmation,booking}_observation` path rather
than an invented payload status.

Duplicate-source breaks carry a `duplicate_source_conflict` proof binding every
source record to one source business key and version while requiring distinct
content hashes. Canonical field comparisons are restricted to the TS-3
source-of-truth paths (for example `/payload/base_currency`,
`/payload/value_date`, `/payload/lifecycle_status`, `/payload/book_id`, and
`/linkage/trade_id`).

Resolved records carry `resolution.evidence_roles` alongside
`resolution.evidence_ids`. A reconciliation resolution must cite a
`RECONCILIATION_RESULT` captured no later than `resolved_at`, and its run ID
must match the break run. It must also carry a structured reconciliation proof
whose family, condition, predicate, source-version set, and field comparisons
are bound to the break and demonstrate the family-specific pass. An
owner-approved non-action resolution is limited to the missing-source and
post-action families, must carry no reconciliation proof, and must cite a
human-approved `DISPOSITION_APPROVAL`.

`examples/trade-break-fixture-matrix.json` plus the manifest-driven tests use
distinct product-specific fixtures for every family and bind each fixture's
`product_context` to the product-specific settlement window. Targeted negative
tests prove cross-family comparison drift, source-operand aliasing, duplicate
source-condition drift, decimal tolerance on exact fields, structured proof
drift, duplicate or unknown resolution IDs, chronology violations, and
unsupported missing-source contexts fail closed.

Break records use immutable record identity: the initial record has
`break_version: 1` and no predecessor; a reopen mints a new non-reused
`break_id`, increments `break_version`, and points `supersedes_break_id` at
the prior record. The reopened record starts at `OPEN`; lifecycle transitions
are evaluated within that new record version.

## Version and identity rules

1. `schema_version` is the contract version and is currently `1.0.0`.
2. `entity_version` versions a canonical entity record. Observation envelopes
   are immutable in TS-3: each correction or late revision receives a new
   `observation_id` linked with `supersedes_observation_id` and uses
   `entity_version: 1`; values greater than one are reserved for a future
   explicitly versioned observation-revision contract. Persistence must retain
   both fields without treating an observation ID as mutable.
3. `source_version` is the source system's monotonic version, represented as a
   decimal-free string to avoid numeric precision loss.
4. `canonical_state_version` versions an immutable canonical projection. A
   future reconciliation run/version remains separate and is not folded into
   this value.
5. Delivery identity is `(source_system, source_event_id)`. Stable source-family
   identity is `(tenant_id, portfolio_id, source_system, observation_kind,
   source_business_key)`; `source_version` is deliberately excluded from that
   key and orders corrections or revisions within the family.
6. Same delivery identity and same content is an idempotent replay. Same
   source identity with different content is a conflict. A greater source
   version is appended and supersedes the active source version; a lower late
   version remains recorded but cannot silently replace it.
7. Cross-tenant or cross-portfolio linkage is rejected. Multiple eligible
   candidates are review-required; no ambiguous candidate populates authority.

## Time and economic semantics

All material timestamps, including payload event times, provenance times, and
canonical timestamps, are timezone-aware. Observation payload availability
times must not be after the envelope `ingest_time`; an FX Forward's future
`value_date` remains an economic date and is not treated as data availability.
Canonical state requires `source_watermark <= as_of_time`, every provenance
reference to be present in `source_version_set`, and every `ingested_at` to be
within the watermark. Each `source_version_set` observation ID is unique even
when source versions differ. Decimal values carry an explicit scale; the Pydantic
model requires the lexical fractional precision to equal that scale. Amount
currencies must match base/terms currencies, and the rate orientation is fixed
to terms currency per base currency. Settlement rule `1.0.0` bounds Spot to a
T+0-to-T+2 business-day envelope represented by at most four calendar days in
this calendar-free contract, while Forward requires a strictly future value
date. Only settlement rule version `1.0.0` is supported in TS-3; unknown
settlement rule versions fail closed until a versioned rule is implemented. A
later calendar-aware rule must be versioned.

## Source-of-truth boundary

`source-of-truth-policy.schema.json` is the machine-readable field ownership
matrix for the exact sixteen MVP field paths: the thirteen canonical fields,
confirmation and booking status, and linkage trade ID. A source is
authoritative only for the facts it owns. Conflicting
source values remain evidence and produce a deterministic conflict outcome;
the canonical projection never resolves disagreement by majority vote or LLM
judgement. Every selected canonical field carries source observation ID,
source/entity versions, timestamps, normalisation and resolution rule versions,
and conflict status.
