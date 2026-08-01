# packages/contracts

TS-3 publishes the canonical FX contract consumed by later generator,
persistence, and reconciliation work. TS-4 adds the deterministic trade-break
taxonomy and lifecycle contracts consumed by later inbox/outbox and
reconciliation work. This package contains no database, transport,
reconciliation, API, or LLM implementation.

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

JSON Schema enforces the closed vocabulary, family-specific combinations, and
permitted lifecycle edges. Pydantic additionally enforces cross-field scope,
source identity, evidence, priority, timestamp, and resolution invariants.
Unknown taxonomy and contract versions fail closed.

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
