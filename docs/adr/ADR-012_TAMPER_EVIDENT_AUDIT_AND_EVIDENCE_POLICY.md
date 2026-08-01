---
title: "ADR-012 — Tamper-Evident Audit and Evidence Policy"
tags: [tradeops-sentinel, adr, audit, evidence, integrity]
status: proposed
created: 2026-07-31
---

# ADR-012 — Tamper-Evident Audit and Evidence Policy

## Status

Proposed. Requires owner approval before implementation.

## Context

TradeOps Sentinel must demonstrate a complete, verifiable evidence trail for
synthetic FX trade-break handling and controlled legacy-booking remediation.
An append-only table by itself is not a sufficient integrity claim: privileged
database/storage actors can change records unless the write model, content
hashes and verification procedure make unauthorised alteration detectable.

The MVP must not claim regulatory-grade WORM storage, legal-hold capability,
absolute immutability, non-repudiation, exactly-once delivery, or real-world
regulatory compliance. Those capabilities are explicitly post-MVP unless
implemented and independently evidenced.

## Decision

### 1. Claim boundary

The MVP may claim **tamper-evident application evidence** only when the
verification procedure below passes. This means the system can detect an
unexpected mutation, deletion, reordering or missing link in records within
the defined evidence scope. It does not mean privileged infrastructure
administrators cannot alter storage, nor that evidence is WORM or legally
admissible.

### 2. Evidence scope

The evidence package for each case includes immutable references to:

- original synthetic source-observation hashes and normalised observations;
- canonical trade-state/reconciliation input and result versions;
- rule, model, prompt, corpus, policy and tool-registry versions;
- agent outputs, tool-call argument/result hashes and citation validation;
- review requests, maker/checker/override decisions and authority checks;
- action draft, signed instruction, dispatch, UiPath receipt, read-back and
  post-action verification; and
- workflow transitions, failures/retries, release build and configuration
  tuple.

### 3. Insert-only audit event stream

`audit_events` is a separate logical append ledger. Application identities may
insert events but may not update or delete them. Normal application identities
also cannot directly update protected evidence indexes or object references.
Migrations and controlled incident-recovery identities are separate from
runtime identities and are themselves audited.

Every event has an immutable event ID, stream ID (`case_id` for case activity;
`release_id` for release evidence), monotonic stream sequence, schema version,
occurred/recorded times, actor/system identity, correlation/causation IDs,
event type, canonical payload hash, previous-event hash and event hash. The
event hash is:

`SHA-256(canonical_encoding_v1(event_header_without_hashes || payload_hash || previous_event_hash))`.

`canonical_encoding_v1` is a versioned deterministic byte encoding defined by
the contract suite; it is not the database's native JSON text representation.
The first event uses a documented stream genesis value. Concurrent append is
serialised per stream so there is one unambiguous predecessor. The design does
not use the hash chain as a distributed consensus mechanism.

### 4. Versioned evidence artefacts

Every retained artefact has: artefact ID/version, content SHA-256, media type,
classification/redaction status, producer, source reference, retention class,
creation time and restricted/original-versus-redacted derivative linkage. An
artefact is addressed by its content hash and never silently overwritten. A
replacement creates a new version with explicit supersession; it does not
rewrite prior evidence.

Source-observation evidence and action manifests carry a typed
tenant/portfolio/case/trade scope. Source-observation references also carry the
observation kind and source system, and the evidence contract requires those
values to match the allowed source identity namespace. A reference cannot be
composed into an action or evidence item from a different scope; the contract
layer rejects that mismatch before any future verifier or dispatcher is called.

Screenshot evidence is optional. Structured observation/read-back and audit
records are required. If a screenshot/receipt is retained, a restricted source
and a redacted derivative are separately hashed; failure to redact or persist
the required evidence changes the workflow to escalation/uncertain state and
prevents verified closure.

### 5. Verification procedure

An independent deterministic `evidence_verifier` must run:

1. before a case reaches `CLOSED`;
2. as part of release-evidence generation; and
3. in a scheduled reconciliation for retained active evidence.

It verifies stream sequence continuity, genesis/previous-hash linkage, event
and artefact hashes, required causal links, object-version references,
case/portfolio scope and the required action lifecycle where an action exists.
Any missing, mutated, re-ordered or unresolvable item produces an
`EVIDENCE_INTEGRITY_FAILURE`, blocks closure/release, and preserves the
observed discrepancy in a new audit event. The verifier has read-only access;
it cannot repair evidence.

### 6. Retention and access

MVP retention duration, access roles, evidence classification and any export
process remain owner decisions. The default technical shape is least-privilege
read/write roles, encryption in transit/at rest provided by the selected
runtime, and redacted representations for LLM/observability use. This ADR does
not claim a legal-hold or WORM configuration.

## Required tests and evidence

| Test | Expected result | Evidence |
| --- | --- | --- |
| Runtime update/delete attempt | Database permission/constraint denies it | Test result and audit rejection |
| Payload, hash or predecessor mutation | Verifier detects exact stream/event failure | Controlled tamper report |
| Event deletion/reordering | Sequence/causal-link verifier fails | Controlled tamper report |
| Missing action/read-back link | Case cannot close; release manifest fails | Workflow and verifier output |
| Artefact content mismatch | Content hash check fails | Artefact-verification report |
| Redaction/persistence failure | No verified closure; escalated/uncertain state | Trace, evidence and state record |
| Privileged recovery procedure | Procedure is separately authorised and auditable | Recovery test record |

Tests that mutate data run against a disposable integrity-test store. They must
not be run against an immutable-retention configuration if one is added later;
storage-configuration validation and mutation-detection logic are separate
tests.

## Consequences

- The MVP gains a precise, reproducible integrity claim and an executable
  evidence-completeness gate.
- Evidence/storage design becomes a shared Honey (logical model), Bumble
  (roles/storage/runtime) and Fizz (independent verification) contract.
- Per-stream serialisation and artefact hashing add modest storage and latency,
  justified by the audit objective.
- A compromised privileged administrator remains outside the guarantee; this is
  disclosed rather than hidden behind the word “immutable.”

## Owner decisions required

1. Evidence retention duration and classification/access policy.
2. Whether a restricted original visual artefact is retained in the MVP or
   whether structured receipt/read-back evidence is sufficient.
3. Whether any external anchoring or WORM/legal-hold capability is desired
   post-MVP; no such claim is allowed without a separate approved ADR and test.
