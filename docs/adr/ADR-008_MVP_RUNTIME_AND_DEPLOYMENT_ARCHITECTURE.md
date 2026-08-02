---
title: "ADR-008 — MVP Runtime and Deployment Architecture"
tags: [tradeops-sentinel, adr, platform, runtime, deployment]
status: draft
created: 2026-07-31
---

# ADR-008 — MVP Runtime and Deployment Architecture

## Status

Proposed. Requires owner approval before implementation. Supersedes the
event/service topology in `PLANS/TRADEOPS_SENTINEL_INFRA_PROPOSAL.md`, which is
marked superseded in-file (kept for history, not for build).

## Context

The prior platform proposal split event handling across Kinesis, local
Redpanda, SQS and Lambda, ran RAG retrieval and a parser as standalone
services, and did not state where ML inference lives in the runtime. Scout's
independent review (`PLANS/TRADEOPS_REVIEW_SCOUT_ON_BUMBLE.md`) found this
premature for MVP scale (P-05), the stream split semantically mismatched
between local and cloud (P-05), multi-source ordering/dedup unspecified (P-04),
ML inference unplaced (P-08), and RAG/parser split into services before any
measured need (P-12). The Architecture Review Report's consolidated finding is
the same: **cut to the smallest architecture that proves the core loop.**

This ADR also owns the runtime seam for the **Action Gateway** introduced by
the review (SB P-01): the only component with any queue/ledger authority for
material actions. It does not redefine the signed-instruction cryptography or
lease/version semantics — those are Honey's ADR-005. It also does not redefine
audit hashing — that is Fizz's ADR-012, referenced here for storage/runtime
placement only.

## Decision

### 1. One runtime, two deployment targets

**Local-first runtime (required for MVP proof):** a single `docker-compose`
stack —

- **PostgreSQL 16 + pgvector** — the only stateful service. Holds canonical
  trade state, `source_event_inbox`/outbox, reconciliation results, cases,
  `action_attempts` ledger, evidence/audit tables, and the RAG/runbook vector
  index.
- **One application container** running the FastAPI/LangGraph app as a set of
  typed internal modules, not separate services: ingestion normalizer,
  deterministic reconciliation engine, ML inference module, the constrained
  LLM investigation workflow (Honey's ADR-004), the RAG/citation adapter, and
  the **Action Gateway** (its own internal code boundary and its own
  least-privilege DB role — see §3).
- **Mock legacy booking application** — a small standalone web app (own
  container) so the Action Gateway/executor boundary is genuinely exercised
  over a network hop, not an in-process call.
- **Local RPA executor** — see ADR-011; not a UiPath dependency for MVP.

**Optional cloud reference path (not required to prove the MVP loop; exists so
the same design has one demonstrated cloud path for the portfolio evidence):**
AWS, single region (proposing `ap-southeast-2` — owner to confirm), the
*same* container images deployed to one ECS Fargate service, RDS PostgreSQL
(single instance, `db.t4g.micro`/`small`), S3 for raw/evidence artefacts,
Secrets Manager, CloudWatch + OpenTelemetry. No Multi-AZ, no cross-region, no
Kubernetes.

### 2. Event handling — no Kinesis, no Redpanda, no Lambda fan-out

Resolves SB P-04/P-05. A single **transactional inbox/outbox pattern in
Postgres** replaces the stream split:

- `source_event_inbox(source_system, source_event_id, content_hash, ...)` —
  unique on `(source_system, source_event_id, content_hash)` so duplicate FIX,
  FpML, or booking-read-back deliveries are absorbed as one semantic
  observation, regardless of arrival order.
- Reconciliation re-runs from the **persisted point-in-time snapshot**, not
  arrival order, so late or duplicate confirmations can't destabilise a result.
- One task queue, implemented as a Postgres table consumed via
  `SELECT ... FOR UPDATE SKIP LOCKED` (case-processing queue, human-review
  notifications, action-attempt dispatch). No Redis, no SQS, no Kafka-API
  broker for MVP — this is the single biggest complexity cut from the prior
  proposal, and it removes the local/cloud parity problem entirely (there is
  only one implementation, not an adapter over two different systems).
- **Post-MVP trigger:** if a measured throughput, fan-out, or replay
  requirement is demonstrated that the SKIP LOCKED pattern can't satisfy, add
  exactly one managed queue (SQS, not Kinesis, unless per-shard ordering at
  volume is proven necessary) behind the same internal queue interface, so the
  swap doesn't touch application code.

### 3. Action Gateway — the only queue/ledger authority for material actions

Resolves SB P-01. The Action Gateway is an internal module with its own
Postgres role (`action_gateway_writer`): it is the only identity permitted to
run ADR-005's eligibility check, write the `action_attempts` ledger row, mark
an instruction `DISPATCHED`, and record the executor's read-back result. The
executor (ADR-011) receives **no AWS credential and no database credential**
— it receives a single work item (signed instruction + correlation ID)
through whatever handoff ADR-011 selects, and returns a typed result to a
Gateway-owned endpoint. This is a runtime/authority boundary, not a
cryptographic design — the instruction lifecycle, signature verification,
expiry, revocation and idempotency-key semantics are Honey's ADR-005; the
Gateway is simply the one runtime identity that enforces them at the point a
real write can be attempted.

**State names follow ADR-005/ADR-013 exactly, not a paraphrase:** `DRAFT` →
`REVIEW_PENDING` → `APPROVAL_COMPLETE` → `SIGNED` → `DISPATCH_ELIGIBLE` →
`DISPATCHED` → `EXECUTION_UNCERTAIN` | `READBACK_PENDING` →
`VERIFIED_APPLIED` | `VERIFIED_NOT_APPLIED` | `ESCALATED`, with `CANCELLED`,
`REVOKED`, `EXPIRED` and `SUPERSEDED` as terminal non-dispatch states. (An
earlier draft of this ADR used an invented `RESERVED` state for the Gateway's
own bookkeeping — that has been removed; the Gateway's atomic reservation
*is* the `DISPATCH_ELIGIBLE`→`DISPATCHED` transition under its own DB role,
not a separate state name.) Completion authority is **read-back-first**
(SB P-03): no state is described as "exactly once" or "provably idempotent"
— the tested claim is at-least-once dispatch with a verified effect, per
ADR-005 §6/§7 and matching the review's explicit ban on unverifiable claims.

**Signing-key hosting (the decision ADR-005 §3 delegates here):** for the
local-first MVP runtime, the Gateway holds a local test signing key (file-
backed, environment-scoped, never exposed to the executor) — sufficient to
exercise the full asymmetric-signature contract without any cloud dependency.
For the optional cloud reference path, the same role is filled by AWS KMS
(asymmetric CMK, Gateway calls KMS to sign/verify, private key material never
leaves KMS). Swapping local-test-key for KMS changes only which
signer implementation the Gateway calls — it does not change ADR-005's
contract, per that ADR's explicit instruction.

### 4. ML inference placement

Resolves SB P-08. For MVP, ML inference is a **versioned in-process module**
loaded by the reconciliation/triage worker — not a standalone model-serving
platform. It loads an immutable model+calibrator+feature-version tuple,
validates compatibility at startup, enforces a bounded inference timeout, and
returns an explicit `MODEL_UNAVAILABLE`/abstain result on any failure — never
a fabricated score, and never a policy/action bypass. **Post-MVP trigger:** an
independently-scaled model-serving need justified by measured release cadence
or scale, not by default.

### 5. RAG and parsing placement

Resolves SB P-12. The RAG retrieval adapter and the FIX/FpML parsing logic are
**modules behind typed interfaces inside the one application container** —
not a standalone Fargate service and not a separate Lambda. Scout's/Honey's
RAG corpus and citation-contract design (ADR-004, ADR-014) is unchanged; only
the deployment boundary is simplified. **Post-MVP trigger:** measured latency,
security isolation, or independent-scaling need.

### 6. Evidence and audit storage placement

Storage decisions here are placement only; the hashing/verification scheme is
Fizz's ADR-012. Evidence artefacts live in **versioned S3** (or local
filesystem/MinIO for the local-first path) — **not** S3 Object Lock compliance
mode by default, since that is operationally irreversible for the retention
period and retention hasn't been decided yet (ADR-012 owns that decision).
`audit_events` uses an insert-only application role with `UPDATE`/`DELETE`
denied at the database-grant level, per ADR-012's insert-only requirement.

### 7. Secrets and CI identity

Secrets Manager (cloud path) / `.env` + docker secrets (local path) for DB
credentials and the Action Gateway's verification key material (private
signing key never leaves the Gateway's environment — public verification
material only where a downstream consumer needs it). CI authenticates to AWS
via OIDC federation for the optional cloud path; no static long-lived keys.

### 8. Recovery scope (folded in here — no separate DR ADR in this round)

Resolves SB P-07. Single-region, single instance is an explicit **scoped
demonstration limitation**, not a resilience claim. MVP recovery objective:
daily RDS automated snapshot, a documented restore runbook that (a) restores
Postgres, (b) replays the source inbox safely (dedup key prevents double
application), (c) re-marks any in-flight `action_attempts` row as
`EXECUTION_UNCERTAIN` pending read-back rather than assuming success, and (d)
re-verifies evidence hash-chain continuity post-restore. This is a tested
restore exercise with a measured restore time, not a shorthand for "backups
exist." Cross-region DR is explicitly post-MVP.

## Consequences

- Component count for MVP: one Postgres, one application container, one mock
  legacy app, one local executor — no managed queue, no separate RAG/parser
  service, no standalone model server. Every remaining component maps to a
  named requirement above.
- Local and cloud deployments run the *identical* container images and the
  *identical* Postgres-based queue/inbox pattern — parity is structural, not
  an adapter that has to be separately tested.
- Throughput ceiling of the SKIP LOCKED pattern is materially lower than a
  managed stream; this is accepted for MVP and explicitly gated behind a
  measured post-MVP trigger before Kinesis (or any managed queue) is added.
- The Action Gateway becomes a named, reviewable component with its own DB
  role — this is new relative to the prior proposal's "Robot polls SQS
  directly," and directly closes SB P-01.
- Recovery is scoped and testable rather than asserted; "RPO ~24h" is no
  longer a shorthand for default backup behaviour.

## Owner decisions required

1. AWS region for the optional cloud reference path (proposing
   `ap-southeast-2`).
2. Whether the optional cloud reference path is built at all for the MVP
   milestone, or deferred until after the local-first vertical slice is
   demonstrated.
3. Monthly cost ceiling for the optional cloud path (materially smaller now
   that Kinesis/Redpanda/Lambda/standalone services are removed).
4. Confirm the recovery ambition level in §8 is acceptable for the portfolio
   demonstration, or raise it (with the understanding that raising it adds
   cost and complexity this ADR deliberately avoided).

## Related ADRs

Depends on / must stay aligned with: ADR-005 (signed action instruction —
owns the cryptography the Action Gateway enforces), ADR-012 (audit/evidence
hashing and retention — owns the claim boundary for storage described in §6),
ADR-011 (this document's companion — owns the executor behind the Action
Gateway), ADR-010 (this document's companion — owns which of the above gets
tested when in CI).
