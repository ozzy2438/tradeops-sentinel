---
title: "ADR-003 — Workflow State Machine and Human-Control Boundary"
tags: [tradeops-sentinel, adr, workflow, human-in-the-loop, maker-checker]
status: draft
created: 2026-07-31
---

# ADR-003 — Workflow State Machine and Human-Control Boundary

## Status

Proposed. Depends on ADR-001/002 and aligns with ADR-005/013. Requires owner
approval before implementation.

## Context

The workflow spans deterministic ingestion/reconciliation, one advisory LLM,
human maker-checker approval and legacy UI automation. Human routing must be a
deterministic policy result; an LLM cannot select an approver, create action
authority or bypass version checks. Durable state is needed for timeouts,
restarts and uncertain execution without claiming exactly-once processing.

## Decision

### 1. Case grain and state ownership

One case is one tenant, portfolio and canonical trade. The workflow service is
the sole writer of case state through expected-version transitions. Source
facts, break facts, approvals, instructions and audit events remain owned by
their deterministic services; workflow state references their exact versions.

### 2. State machine

The permitted primary path is:

`OBSERVATION_RECEIVED → INPUT_VALIDATED → TRADE_STATE_ASSEMBLED →
RECONCILED → NO_BREAK_CLOSED | BREAK_OPENED → PRIORITISED →
INVESTIGATING → RECOMMENDATION_VALIDATING → RECOMMENDATION_READY |
AGENT_NEEDS_EVIDENCE | AGENT_ABSTAINED | SECURITY_ESCALATED →
READY_FOR_POLICY → NO_ACTION_REVIEW | DRAFT → REVIEW_PENDING →
APPROVAL_COMPLETE → SIGNED → DISPATCH_ELIGIBLE → DISPATCHED →
READBACK_PENDING | EXECUTION_UNCERTAIN → POST_ACTION_VERIFYING →
VERIFIED_APPLIED → FINAL_RECONCILIATION → CLOSED`.

Within `REVIEW_PENDING`, the required human sub-states are
`MAKER_REVIEW_PENDING → MAKER_APPROVED → CHECKER_REVIEW_PENDING →
CHECKER_APPROVED`. Only then may the action transition to
`APPROVAL_COMPLETE`.

Alternate terminal or controlled states are:

- `INPUT_REJECTED`, `LINKAGE_REVIEW_PENDING`, `MORE_EVIDENCE_REQUIRED`;
- `MAKER_REJECTED`, `CHECKER_REJECTED`, `CANCELLED`, `REVOKED`,
  `EXPIRED`, `SUPERSEDED`;
- `VERIFIED_NOT_APPLIED`, `POST_ACTION_FAILED`, `ESCALATED`; and
- `CLOSED_NO_ACTION` after an authorised, reasoned disposition.

The material action sub-lifecycle is governed by ADR-005/013. A state name in
this ADR never grants authority beyond those contracts.

### 3. Transition guards

Every transition records case version, from/to state, event type, actor,
correlation ID, timestamp, policy/rule/schema versions and causal evidence.
Transitions use expected-version compare-and-set. Key guards include:

- investigation requires a frozen same-case evidence snapshot;
- recommendation readiness requires schema and deterministic citation checks;
- policy requires current canonical, reconciliation and evidence versions;
- maker/checker states use the deterministic route returned by policy;
- checker must differ from maker and possess the required scoped role;
- deterministic draft compilation uses policy-allowed fields and exact locked
  evidence before review; signing occurs only after maker-checker approval;
- dispatch requires both eligibility checks in ADR-005/013;
- closure requires post-action read-back, reconciliation and audit-completeness
  checks, not an LLM or executor success signal.

Any new material source/canonical/booking/policy version supersedes stale review
or action authority and returns the case to `RECONCILED`, `SUPERSEDED`
or `ESCALATED` as defined by policy.

### 4. Deterministic human-review routing

The policy engine computes:

- whether review is required;
- maker/checker role and portfolio scope;
- materiality class and permitted action type;
- review expiry, separation-of-duty constraint and required evidence versions;
- idempotency key for the review request.

The LLM may recommend a resolution or abstain. It cannot choose
`requested_role`, nominate an approver or create a review request. The workflow
creates an idempotent interrupt only from the locked policy route.

All booking writes use maker-checker in the MVP. Material economic-field
changes remain manual-only unless ADR-005 final-submit concurrency controls are
approved and proved. Maker and checker cannot be the same identity or agent.

### 5. Overrides and prohibited bypass

An authorised human override records identity, role, reason, prior decision,
scope, expiry and exact evidence/policy versions. Override may reject, request
more evidence, choose manual handling, escalate or select an owner-approved
alternative. It cannot:

- waive maker-checker for a booking write;
- authorise a forbidden field or stale instruction;
- bypass signature, expiry, revocation, version/lease or read-back checks;
- make an LLM or executor an approver; or
- close a failed reconciliation or incomplete evidence package.

Risk acceptance for a release is governed separately by ADR-014 and does not
create action authority.

### 6. Resumability, timeout and failure

- Each transition and external intent is durably committed before work
  continues; inbox/outbox processing is at-least-once with deterministic
  deduplication.
- Human and tool waits resume from stored state and exact locked versions.
- LLM/tool timeout moves to `AGENT_ABSTAINED` or
  `MORE_EVIDENCE_REQUIRED`; approval timeout expires the review and blocks
  dispatch.
- A dispatch ambiguity moves to `EXECUTION_UNCERTAIN` and follows the
  read-back-first recovery in ADR-005/013. No blind write retry is permitted.
- A failed audit/evidence write prevents closure and escalates.

### 7. Explicitly prohibited autonomous actions

No agent may alter canonical/source data, create or sign an instruction,
approve/reject a review, dispatch RPA, change a production-like booking,
change taxonomy/policy, activate a rule, or close a case. No component receives
generic database write, arbitrary SQL, shell or open-web authority.

## Required tests and evidence

- A transition-table contract test rejects every undeclared edge.
- Role tests reject arbitrary, stale, duplicate, cross-portfolio and
  same-maker/checker review attempts.
- Restart tests resume every interrupt without repeating a semantic action.
- Timeout tests fail closed for LLM, tool, review, signing and dispatch waits.
- New-observation/policy/booking-version tests supersede stale approvals.
- Negative-capability tests prove no LLM/tool path can sign, approve, dispatch
  or directly mutate a booking.
- Success and uncertain-execution scenarios both reach a fully evidenced safe
  disposition.

## Consequences

- Deterministic routing removes an unsafe agent side effect and makes approval
  tests reproducible.
- The explicit state graph is larger than a happy-path flow but exposes
  revocation and uncertainty instead of hiding them.
- One case per trade/portfolio and one LLM workflow reduce MVP complexity.

## Owner decisions required

1. Approve maker/checker roles, separation-of-duty rules and review expiry.
2. Approve materiality classes, manual-only economic fields and escalation
   authority.
3. Approve allowed human override dispositions and roles.
4. Approve workflow timeout budgets and the owner of unresolved escalations.

## Review findings closed

Closes Fizz-on-Honey H-03 and M-03/M-04, plus the Architecture Review findings
on deterministic routing, case grain, resumability and human authority.
