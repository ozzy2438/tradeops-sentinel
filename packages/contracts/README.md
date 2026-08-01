# packages/contracts

TS-3 publishes the canonical FX contract consumed by later generator,
persistence, and reconciliation work. It contains no database, transport,
reconciliation, API, or LLM implementation.

## Contract layers

- `schemas/*.schema.json` are Draft 2020-12 interchange schemas.
- `models.py` contains strict Pydantic v2 models for the same TS-3 documents
  and cross-field validation that JSON Schema cannot express by itself.
- `examples/` contains valid Spot/Forward observations and canonical records,
  plus intentionally invalid fixtures for the negative contract tests.
- `tests/test_contracts.py` validates every example against both layers and
  exercises replay, provenance, scope, version, and temporal semantics.

## Version and identity rules

1. `schema_version` is the contract version and is currently `1.0.0`.
2. `entity_version` versions an observation or canonical entity record.
3. `source_version` is the source system's monotonic version, represented as a
   decimal-free string to avoid numeric precision loss.
4. `canonical_state_version` versions an immutable canonical projection. A
   future reconciliation run/version remains separate and is not folded into
   this value.
5. Delivery identity is `(source_system, source_event_id)`. Source identity is
   `(tenant_id, portfolio_id, source_system, observation_kind,
   source_business_key, source_version)`.
6. Same delivery identity and same content is an idempotent replay. Same
   source identity with different content is a conflict. A greater source
   version is appended and supersedes the active source version; a lower late
   version remains recorded but cannot silently replace it.
7. Cross-tenant or cross-portfolio linkage is rejected. Multiple eligible
   candidates are review-required; no ambiguous candidate populates authority.

## Time and economic semantics

`event_time`, `effective_time`, and `ingest_time` are separate timezone-aware
timestamps. Only `event_time <= ingest_time` is required: an effective date may
legitimately be future-dated for an FX Forward. Decimal values carry an
explicit scale; the Pydantic model requires the lexical fractional precision to
equal that scale. Amount currencies must match base/terms currencies, and the
rate orientation is fixed to terms currency per base currency. No market
calendar or economic tolerance is inferred in TS-3.

## Source-of-truth boundary

`source-of-truth-policy.schema.json` is the machine-readable field ownership
matrix. A source is authoritative only for the facts it owns. Conflicting
source values remain evidence and produce a deterministic conflict outcome;
the canonical projection never resolves disagreement by majority vote or LLM
judgement. Every selected canonical field carries source observation ID,
source/entity versions, timestamps, normalisation and resolution rule versions,
and conflict status.
