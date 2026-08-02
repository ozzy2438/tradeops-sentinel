---
title: "ADR-005 — Signed Action Instruction and Verification Contract"
tags: [tradeops-sentinel, adr, signed-action, idempotency, rpa, verification]
status: draft
created: 2026-07-31
implemented_by: ["#5"]
---

# ADR-005 — Signed Action Instruction and Verification Contract

## Status

Proposed. Depends on ADR-001/003/004 and is safety-test aligned with ADR-013.
Owner approval and demonstrated final-submit concurrency evidence are required
before any RPA write.

## Context

A legacy UI has no modern write API and cannot provide an honest exactly-once
guarantee. An instruction may become stale after approval, a queue message may
race with revocation, and a UI timeout may hide whether a save occurred. The
MVP therefore needs a narrow, version-bound capability whose authority can be
checked twice and whose outcome is established only by read-back and
reconciliation.

## Decision

### 1. MVP action scope

The only proposed automated MVP write is:

`SET_CONFIRMATION_REFERENCE`

It may update the mock booking's non-economic `confirmation_reference` from an
expected old value to the exact value proven by the approved FpML-style
confirmation. All booking writes still require maker-checker.

Base/terms currencies, side, amounts, rate, trade date, value date,
counterparty and other material economics are manual-only in MVP. Adding any
field or action type requires a versioned allow-list change, owner approval,
threat/contract tests and release evidence.

### 2. Instruction payload

A versioned `SignedActionInstruction` contains:

- `instruction_id`, `instruction_schema_version`, `action_type`;
- tenant, portfolio, case, trade and target booking IDs;
- canonical-state, source-observation, reconciliation, recommendation,
  policy, maker-decision and checker-decision versions;
- target field path, normalised expected old value and exact approved new value;
- target booking version and required final-submit control (`CAS` or `LEASE`);
- issue time, not-before time, expiry and single-use nonce;
- deterministic idempotency key and action-draft content hash;
- signer key ID, signature algorithm and signature over canonicalised payload;
- cancellation/revocation lookup reference and evidence-manifest reference.

The deterministic action compiler—not the LLM—constructs this exact draft from
the locked policy route and approved FpML evidence before human review. Maker
and checker approve or reject the exact payload hash; any later payload change
creates a new draft and restarts review. Unknown fields, action types or
payload versions are rejected.

The idempotency key binds tenant, portfolio, trade, action type, target booking
version, normalised old/new values and draft hash. At-least-once delivery is
expected; the claim is at-most-one semantic action per key, not exactly once.

Consumed source-observation references and the evidence-manifest reference carry
the same tenant/portfolio/case/trade scope as the instruction. Source
observations are typed by observation kind and source system, and their IDs use
the existing `obs_<kind>_...` namespace. The consumed source set is treated as
unordered for identity and sorted by a documented stable tuple before draft
hashing. Typed timestamps, and strings at the four explicitly-known action
timestamp paths (`issued_at`, `not_before`, `expires_at`, and
`final_submit_control.lease_expires_at`), are canonicalised to UTC with `Z`
before hashing, including when mapping inputs use a non-zero offset. Arbitrary
approved old/new value strings remain byte-exact; a string is never treated as
a timestamp merely because it resembles ISO-8601.

### 3. Signature and key boundary

The instruction uses an asymmetric signature with an explicit key ID and
algorithm. The signer holds the private key outside the agent and executor
contexts; the executor/action gateway receives only trusted public-key material.
A local test key or optional cloud KMS implementation is chosen by ADR-008,
without changing this contract. Shared HMAC secrets in the executor are
prohibited.

Signing occurs only after the exact draft hash has current maker-checker
approval and policy, evidence versions, separation of duties, allowed
field/action and expiry are validated. Signing does not make an instruction
permanently executable.

### 4. Lifecycle and dispatch eligibility

The action lifecycle is the ADR-013 lifecycle:

`DRAFT → REVIEW_PENDING → APPROVAL_COMPLETE → SIGNED →
DISPATCH_ELIGIBLE → DISPATCHED → EXECUTION_UNCERTAIN | READBACK_PENDING →
VERIFIED_APPLIED | VERIFIED_NOT_APPLIED | ESCALATED`.

`CANCELLED`, `REVOKED`, `EXPIRED` and `SUPERSEDED` are terminal
non-dispatch states.

The deterministic eligibility service runs immediately before queue
publication and again immediately before the Playwright executor's UI write.
It verifies:

- signature, trusted/revoked signer key, schema, nonce and expiry;
- current maker/checker identities, scope, separation and decision status;
- policy/action allow-list and exact source/canonical/reconciliation versions;
- no revocation, cancellation, supersession or competing active lease;
- current booking ID/version and expected old value; and
- evidence availability and audit preconditions.

A queue message is intent, not authority. The executor receives no database or AWS
identity that can bypass the action gateway's bounded eligibility decision.

### 5. Revocation, cancellation and supersession

An authorised policy/approval service creates an append-only revocation or
cancellation record with actor, role, reason, scope, time and causal versions.
Queue deletion is attempted for hygiene but is never the safety control. The
second eligibility check observes current revocation state.

New material observations, changed booking/canonical/reconciliation/policy
versions, expired approval, revoked signer key or a replacement action
supersede the instruction. A terminal instruction cannot return to dispatch.

### 6. Read-back-before-write and concurrency

Before editing, the automation obtains a fresh booking read-back and confirms
the target record, expected old value, booking version and unchanged
non-target field fingerprint.

At final submit the mock legacy application must enforce either:

1. an optimistic version/compare-and-set token; or
2. an exclusive, expiring per-booking action lease validated at submit.

A pre-read alone is insufficient. If neither mechanism is available, the executor
write is disabled and the case is manual-only. A conflict or expired lease
causes no write and moves to `ESCALATED`/`SUPERSEDED`.

### 7. Post-action verification and uncertainty

After an apparent save the system performs a fresh read-back, compares all
observed fields to the pre-read and instruction, and runs reconciliation:

- target equals approved new value, no unauthorised field changed and relevant
  reconciliation passes → `VERIFIED_APPLIED`;
- target remains expected old value and authority is still current →
  `VERIFIED_NOT_APPLIED`; a bounded re-dispatch may be requested only through
  fresh eligibility using the same idempotency key;
- any other value/version, read-back failure, unexpected field change, expired
  or revoked authority → `ESCALATED`.

UI timeout, lost session, ambiguous save indication or missing receipt creates
`EXECUTION_UNCERTAIN`. Recovery is read-back first; blind retry is prohibited.
Executor success status alone cannot close a case.

### 8. Evidence

The evidence package references:

- canonical instruction bytes/hash and signature verification result;
- policy, maker/checker and eligibility decisions;
- pre-read and post-read structured observations and hashes;
- lease/CAS result, executor attempt metadata and any ambiguity;
- changed-field diff, post-action reconciliation and final disposition; and
- complete action/audit event sequence under ADR-012.

If required evidence cannot be persisted or verified, closure fails and the
case escalates. This is a tamper-evident claim under ADR-012, not a
regulatory-grade immutable/WORM claim.

## Required tests and evidence

ADR-013 supplies the complete failure matrix. Contract acceptance additionally
requires:

- canonical serialisation/sign/verify and key-revocation vectors;
- rejection of every field/action outside the one-item MVP allow-list;
- maker/checker separation and stale-version rejection;
- eligibility checks on both sides of a queue/revocation race;
- final-submit CAS/lease race proving no intervening update is overwritten;
- duplicate delivery proving no duplicate semantic write;
- UI-timeout-after-save recovery by read-back, with no blind retry;
- pre/post changed-field diff and fresh reconciliation before closure.

No executor write may run until these controls are available in an approved mock
environment; deterministic contract simulators may be used in CI first.

## Consequences

- The one-field action is deliberately narrow but proves approval, signing,
  revocation, concurrency, RPA and verification end to end.
- The legacy mock must expose a testable CAS/lease seam. Otherwise the write
  portion remains manual-only without weakening the rest of the MVP.
- Asymmetric signing and two eligibility checks add components, but each exists
  to prevent a specific stale/replay/bypass failure.

## Owner decisions required

1. Approve `SET_CONFIRMATION_REFERENCE` as the sole automated MVP action and
   keep economic fields manual-only.
2. Choose the final-submit control: optimistic CAS, expiring lease, or
   manual-only execution.
3. Approve instruction/review expiry, bounded retry and timeout values.
4. Approve signer, revocation/cancellation authorities and escalation owner.
5. Approve local test-key versus optional cloud signing implementation through
   ADR-008, without changing the asymmetric trust boundary.

## Review findings closed

Closes Fizz-on-Honey H-01/H-02/M-02/M-08 and Scout-on-Bumble P-02/P-03,
aligned to ADR-013's failure, replay, revocation and uncertain-execution tests.
