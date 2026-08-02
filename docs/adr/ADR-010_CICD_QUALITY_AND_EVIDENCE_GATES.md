---
title: "ADR-010 — CI/CD Quality and Evidence Gates"
tags: [tradeops-sentinel, adr, ci-cd, evidence, quality-gates]
status: draft
created: 2026-07-31
implemented_by: ["#2"]
---

# ADR-010 — CI/CD Quality and Evidence Gates

## Status

Proposed. Requires owner approval before implementation.

## Context

`PLANS/TRADEOPS_SENTINEL_GITHUB_CICD_PROPOSAL.md` proposed a tiered pipeline
(per-PR / on-merge / release-candidate / scheduled) to resolve two findings
from my own review of Fizz's assurance design
(`PLANS/TRADEOPS_REVIEW_BUMBLE_ON_FIZZ.md`): no CI-trigger mapping existed
(BF-3), and the UiPath failure-injection suite can't run reliably as an
ephemeral per-PR gate (BF-2). ADR-011 has since removed UiPath from the MVP
entirely (Playwright substitute), which changes what "needs a future
environment" actually means for MVP — this ADR restates the pipeline for the
approved MVP vertical slice specifically, per Orchestrator's instruction to
separate gates runnable now from gates requiring a future cloud or execution
environment.

## Decision

### 1. Gates that run now — every pull request, local-only, no cloud dependency

All of these run against the docker-compose stack (ADR-008 §1) on any
runner, including a laptop:

1. Formatting + lint (ruff, terraform fmt where infra files exist).
2. Type checking (mypy).
3. Unit tests.
4. Contract tests — Pydantic schema validation for every cross-workstream
   contract (Honey's canonical model, action-instruction payload, evidence
   item).
5. Schema validation — FIX/FpML fixture parsing against the defined schemas,
   including deliberately malformed fixtures (expect rejection, not crash).
6. Reconciliation-invariant tests — Honey's break taxonomy (ADR-002) run as
   deterministic fixtures against the reconciliation engine; no external
   dependency, pure logic.
7. Action-contract and idempotency tests — signature verification, expiry,
   revocation, and idempotency-key replay/duplicate rejection (ADR-005),
   run against a **mocked** Action Gateway and a **mocked** executor
   response — not the real Playwright/mock-app stack, so this stays fast.
8. LLM evaluation tests (Scout's ADR-007 policy) — citation-correctness and
   structured-output-validity checks against **fixture-authored expected
   citations**, deterministic ID matching, not an LLM-judge scorer (this was
   Critical finding BF-1 in my review of Fizz's plan; it applies unchanged
   here as the MVP's single LLM workflow). Per ADR-007 §5, **every fixture in
   the release corpus runs three independent times**, and a single failed
   invocation fails the gate — this closes ADR-007's delegated question of
   "what CI tier" (its owner decision 5): the whole suite is fixture-based
   and local (no cloud/UiPath dependency), so it stays in this per-PR tier
   even at 3x repetition; only the release-candidate manifest assembly
   (step 19) additionally records the 3x results as evidence.
9. Security and secret scanning (`gitleaks`).
10. Dependency scanning (`pip-audit`).
11. Container build (no scan yet — scan is step 14, see below — build alone
    can run without cloud).
12. `terraform validate` (not `plan` — `plan` needs cloud credentials; see
    §2) for any infra files present.

### 2. Gates that need the docker-compose integration stack — every PR, still local

These need the full local stack (Postgres + app + mock legacy app +
Playwright executor) running in the CI runner via docker-compose, but still
require **no cloud account and no paid resource**:

13. Integration tests: state-machine resume/checkpoint, human-interrupt
    simulation with mocked distinct maker/checker identities, cross-portfolio
    isolation tests (Fizz's ADR-013).
14. Container vulnerability scan (Trivy) against the images built in step 11.
15. **End-to-end action-execution test against the real mock app +
    Playwright executor** — this is new relative to the prior UiPath-era
    design: because ADR-011 makes the MVP executor a free, deterministic,
    headless-browser tool, the *entire* signed-instruction →
    dispatch → uncertain-execution → read-back → re-reconciliation loop
    (including Fizz's ADR-013 failure-injection scenarios: stale approval,
    revoked approval, concurrent record change, UI timeout after apparent
    save, read-back mismatch) can run **on every PR**, not gated to
    merge/release-candidate as the prior UiPath-based design required. This
    is a direct, material simplification the executor swap in ADR-011
    enables.
16. Evidence/audit-integrity tests (Fizz's ADR-012 verifier) against a
    disposable test store — insert-only role enforcement, hash-chain
    detection of tamper, per ADR-012 §"required tests."
17. Evidence-manifest generation — the `release_evidence` manifest structure
    is assembled and validated for completeness (all required fields
    present) on every PR, even though the release-gate decision (step 19)
    only fires at a release-candidate tag.

### 3. Gates that require the optional cloud reference path — on-merge or scheduled only

These need the AWS reference environment (ADR-008 §1) and are not part of
the fast per-PR loop:

18. `terraform plan` against the real cloud environment (posted as a PR
    comment on infra-touching PRs, applied only via the CD promotion flow).
19. **Evaluation-gate / release-evidence hard-fail check** — assembled at a
    release-candidate tag (`vX.Y.Z-rc.N`), consuming the manifest from step
    17 plus any cloud-environment smoke-test results. Hard-fails
    independent of any numeric threshold on: failed reconciliation
    invariant, unauthorised/write-capable tool attempt, maker-checker/
    materiality bypass, invalid or replayed action instruction, unsupported
    required citation, raw sensitive data in prompt/trace, cross-portfolio
    leakage, duplicate semantic action, incorrectly-closed uncertain
    execution, incomplete/tampered audit chain (per Fizz's ADR-012
    verifier), or a missing required evidence artefact. An associated
    signed, scoped, expiring **exception record** (resolving my review
    finding BF-5) can waive a specific named condition; CI validates its
    signature, scope match, and expiry before treating anything as waived.
    Metric-regression comparisons (beyond the hard-fail list) are enabled
    only once the owner approves baseline values/tolerances/minimum sample
    sizes per Scout's ADR-007.
20. Scheduled multi-day operating-proof run (drift/latency/cost snapshots,
    incident-drill exercise) — this is explicitly out of Sprint 1 scope per
    the owner's phasing and only becomes relevant once a release candidate
    exists.

## Consequences

- Because ADR-011 replaced UiPath with a free, deterministic local
  executor, the MVP's full action-safety test suite (step 15) moves from
  "merge/release-candidate only" (as I proposed when UiPath was in scope) to
  "every PR" — a meaningfully stronger per-PR guarantee at zero additional
  infrastructure cost. This is the single biggest CI simplification the
  MVP reset produces.
- Nothing in §1–2 requires an AWS account, a paid resource, or a UiPath
  licence — Sprint 1 (Epic 6, CI foundation) can be built and demonstrated
  entirely locally, consistent with the owner's instruction that no cloud
  provisioning or paid resource may start yet.
- §3's release-candidate gate is the only place the full `release_evidence`
  hard-fail mechanism applies — this is deliberately not a per-PR gate
  (matches the resolved BF-3 trigger-tiering finding), so day-to-day
  development speed isn't traded against release rigor.
- If UiPath is later activated (ADR-011 §3), its failure-injection suite
  re-enters at "on-merge, persistent harness" per the original tiering in
  `PLANS/TRADEOPS_SENTINEL_GITHUB_CICD_PROPOSAL.md` §3.13 — that guidance is
  not deleted, just not currently load-bearing for MVP.

## Owner decisions required

1. Approve the tiering above, specifically that action-safety tests (step
   15) run per-PR now that the executor is Playwright, not UiPath.
2. Approve the exception-record mechanism (signed/scoped/expiring) as the
   only way to waive a hard-fail condition at the release-candidate gate.
3. Confirm metric-regression comparisons stay disabled until Scout's
   ADR-007 baselines are approved — CI reports metrics from day one but
   does not gate on them until then.

## Related ADRs

ADR-008 (runtime the pipeline tests against), ADR-011 (executor choice that
changes step 15's gating tier), ADR-005/ADR-013 (contracts and failure
scenarios step 7/15 test against), ADR-012 (evidence verifier step 16/19
consume), ADR-007 (Scout — evaluation policy step 8 tests against, and the
future metric-baseline gate in step 19), ADR-009 (this document's companion —
owns where these workflow files live and how they're protected).
