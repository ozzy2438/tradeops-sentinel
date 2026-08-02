---
title: "ADR-013 — Failure, Replay, Revocation, and Uncertain-Execution Safety"
tags: [tradeops-sentinel, adr, idempotency, rpa, recovery, safety]
status: draft
created: 2026-07-31
---

# ADR-013 — Failure, Replay, Revocation, and Uncertain-Execution Safety

## Status

Proposed. Depends on ADR-003 and ADR-005; requires owner approval before
implementation.

## Context

Message systems, human approval, legacy UI automation and distributed recovery
cannot honestly promise exactly-once execution. The MVP must instead make every
material side effect idempotency-keyed, version-bound, revocable before action,
read-back verified and safe under duplicate delivery, timeout, restart and
concurrent record change.

## Decision

### 1. Delivery and idempotency model

The platform uses at-least-once message delivery with deterministic deduplication;
it does not claim exactly-once delivery.

- A source observation has a source-unique ID/version or a canonical source
  fingerprint. The inbox records it before processing; duplicates return the
  original observation/result reference and do not create a new semantic event.
- A material action has one immutable `instruction_id` and `idempotency_key`.
  The key binds tenant, portfolio, trade, action type, target booking version,
  exact normalised old/new values and action-draft hash. One key may reach one
  terminal disposition only.
- Inbox, action and audit writes are transactionally recorded with an outbox
  intent. Consumers may redeliver messages, but transitions use expected
  version plus idempotency key and reject duplicate/conflicting state.

### 2. Action lifecycle and dispatch eligibility

An executable instruction may follow only this material action lifecycle:

`ACTION_COMPILED → ACTION_SIGNED → DISPATCH_ELIGIBLE → ACTION_DISPATCHED →
EXECUTION_UNCERTAIN | READBACK_PENDING → VERIFIED_APPLIED |
VERIFIED_NOT_APPLIED | ESCALATED`.

`CANCELLED`, `REVOKED`, `EXPIRED` and `SUPERSEDED` are terminal non-dispatch
states. No terminal state can transition to dispatch.

This is the **instruction sub-lifecycle**, not a second case-workflow state
machine; its names are the matching ADR-003 case states. Maker/checker review
occurs before `ACTION_COMPILED` through ADR-003's deterministic policy route.
The reviewable `proposed_resolution` is non-executable. Only after the required
approvals does the deterministic action compiler create an executable draft.
Terminal instruction dispositions map to `ACTION_CANCELLED`,
`ACTION_REVOKED`, `ACTION_EXPIRED` and `ACTION_SUPERSEDED`.

Before queue publication **and** immediately before UiPath performs a UI write,
a deterministic `dispatch_eligibility` service revalidates:

- action/instruction status, expiry, nonce and idempotency key;
- signature and signing-key status;
- maker/checker identities, scope, expiry and revocation state;
- policy version/status and materiality route;
- exact canonical/reconciliation/recommendation/approval versions;
- current target booking version and expected old value; and
- absence of a cancellation, supersession or competing active action lease.

Any failure cancels/supersedes the instruction or escalates the case. A queue
message is an intent, not authority: cancellation racing with message delivery
is resolved by the consumer-side second eligibility check.

### 3. Concurrency control

The mock legacy booking application must demonstrate one of these enforceable
controls for an RPA-write-eligible field:

1. an optimistic version/compare-and-set token checked at the final submit; or
2. an exclusive, expiring per-booking action lease acquired and validated by
   the gateway/legacy app at final submit.

The robot must perform read-back-before-write and validate expected version and
old value. If the final-submit control is unavailable or validation fails, it
does not write and moves to `ESCALATED`. Material-economic fields are
manual-only in the MVP unless the owner approves the control and its test
evidence. A read-before-write check alone is not considered concurrency safe.

### 4. Revocation and cancellation

The policy/approval service can create an append-only revocation record for a
signed instruction, with actor, reason, scope, timestamp and causal evidence.
Revocation publishes a cancellation intent but does not rely on queue deletion
for safety. All dispatch consumers must consult the current eligibility state.
New observations, changed booking versions, expired approval, policy
suspension, replaced action draft or explicit human cancellation create a
`SUPERSEDED` or `REVOKED` disposition and invalidate future dispatch.

### 5. Uncertain execution and recovery

Timeout, session loss, ambiguous UI confirmation or missing receipt after
dispatch produces `EXECUTION_UNCERTAIN`. The system never blindly retries the
write. Recovery performs a read-only booking read-back and full relevant
reconciliation:

- authorised new value and clean applicable reconciliation →
  `VERIFIED_APPLIED`;
- expected old value still present and eligibility still valid → a new bounded
  dispatch attempt may be requested, using the original idempotency key and
  fresh dispatch eligibility;
- another value/version, read-back failure, evidence gap or expired/revoked
  authority → `ESCALATED`.

UiPath success status alone never closes a case. Closure requires read-back,
post-action verification, evidence verification and audit completeness.

### 6. Partial workflow failure

Every external side effect has durable pre- and post-side-effect transition
records. On process restart, the recovery controller resumes from the last
committed state and re-runs only read-only/pure work unless dispatch eligibility
permits a bounded action attempt. A failed audit/evidence write prevents
verified closure and is escalated; it is not repaired silently.

## Required tests and evidence

| Scenario | Required assertion |
| --- | --- |
| Duplicate source event | One observation/reconciliation semantic result; duplicate record is auditable. |
| Duplicate action delivery | One action disposition; duplicate consumer attempts do not re-write. |
| Stale approval / changed case version | Eligibility rejects dispatch and records supersession. |
| Revoked approval after queue publish | Robot-side eligibility rejects the queued instruction. |
| Queue cancellation race | Either cancellation wins or execution proceeds only if both checks were valid; no ambiguous silent closure. |
| Concurrent booking change | Final-submit version/lease fails; no unintended overwrite. |
| UI timeout after apparent save | `EXECUTION_UNCERTAIN`, read-back-first resolution, no blind retry. |
| Read-back mismatch | Escalation with exact expected/actual evidence. |
| Signature failure / revoked signer key | No dispatch; failure is audited. |
| Expired instruction | No dispatch or retry. |
| Partial crash at each external boundary | Restart reaches a deterministic safe state without duplicate semantic action. |

The MVP's Playwright executor cases run in the local docker-compose CI stack on
every pull request because the mock application's selectors and failure modes
are controlled. A real UiPath-specific harness is post-MVP and cannot be
claimed by these tests until its environment, licence and evidence path are
separately approved.

## Consequences

- The action path gains an honest at-least-once/at-most-one-semantic-action
  safety model rather than an unprovable exactly-once claim.
- The mock legacy application must support a demonstrable version/lease seam or
  material actions remain manual-only, reducing scope but preserving safety.
- Revocation and recovery add states, tests and storage records but eliminate
  blind retry and stale queued-action behaviour.

## Owner decisions required

1. Approve the final-submit version/lease mechanism, or select manual-only
   material changes for MVP.
2. Approve which fields/actions are eligible for controlled RPA after the above
   control is proven.
3. Approve revocation authorities, cancellation reasons and escalation owner.
4. Approve the allowable retry budget and timeout policy for the mock runtime.
