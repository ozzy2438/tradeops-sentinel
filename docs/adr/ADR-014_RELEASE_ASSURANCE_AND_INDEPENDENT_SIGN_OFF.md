---
title: "ADR-014 — Release Assurance and Independent Sign-Off"
tags: [tradeops-sentinel, adr, release-governance, ci-cd, assurance]
status: draft
created: 2026-07-31
---

# ADR-014 — Release Assurance and Independent Sign-Off

## Status

Proposed. Depends on ADR-007, ADR-009 and ADR-010; requires owner approval
before implementation.

## Context

TradeOps Sentinel must deliver real engineering evidence without manufactured
GitHub activity or self-certification. Gates must be reproducible and capable
of failing closed, but must not make every pull request depend on expensive,
flaky GUI, cloud or model workloads. A release decision requires a factual
evidence bundle and a clearly accountable human authority.

## Decision

### 1. Evidence tuple and release manifest

Every gate evaluates a versioned configuration tuple:

`commit_sha, build_id, dependency_lock_hash, contract_schema_versions,
rule_versions, prompt_version, model_or_evaluator_version, corpus_version,
tool_registry_version, policy_version, environment, test_corpus_manifest`.

The pipeline creates a machine-readable `release_evidence` manifest that
records this tuple, each suite result, metric/threshold/CI where applicable,
known defects/exceptions, audit/trace coverage, artefact hashes/locations,
evaluator identity and generation time. The manifest is evidence, not a waiver;
it is invalid if required fields or referenced artefacts are missing.

### 2. Tiered quality gates

| Tier | Trigger | Required checks | What it cannot claim |
| --- | --- | --- | --- |
| T0 — local/PR | Every feature branch and pull request | formatting, lint/type, unit, schema/contract, deterministic reconciliation invariant, action-contract/idempotency simulators, secret/dependency scan, manifest schema validation | Cloud, UiPath, operating-proof or production-like validation |
| T1 — merge integration | Protected-main merge / scheduled integration | full deterministic scenario corpus, citation fixtures, structured-output/abstention fixtures, injection fixtures, audit/evidence verifier, container/IaC validation, and the Playwright/mock-legacy-app success plus uncertain-execution drills | UiPath-specific or cloud-deployment validation |
| T2 — release candidate | Signed/tagged candidate after owner-approved environment exists | T0/T1 green, pinned artefacts, independent evidence review, re-run of the MVP Playwright safety drills, and deployment evidence only if an optional cloud path is actually used | Multi-day operating proof, UiPath integration, or production proof unless separately completed |
| T3 — operating proof | Scheduled approved window | synthetic run continuity, SLO/latency/cost snapshots, recovery/incident drill, trace/audit sampling and postmortem | Real customer, live-market or regulatory certification |

Cloud- and UiPath-dependent checks are explicitly deferred until their approved
environment exists. The MVP Playwright executor is not evidence of a UiPath
integration, and no local simulation is described as proof of cloud deployment.

### 3. Unconditional hard failures

Regardless of numerical thresholds, a candidate cannot advance if any required
test proves: unauthorised/write-capable agent tool execution; policy or
maker-checker bypass; invalid/replayed/expired/revoked action instruction;
reconciliation-invariant failure; unsafe prompt-injection outcome; missing or
unsupported required citation; raw secret/sensitive test value in a prompt or
trace; cross-case/portfolio leakage; duplicate semantic action; unsafe handling
of uncertain execution; incomplete/tampered evidence chain; or a required
evidence artefact/manifests missing.

Citation correctness uses fixture-authored expected citation IDs, source
versions/locations and deterministic matching as the release-blocking baseline.
Any LLM-as-judge score is optional/advisory and cannot be the sole evidence of
grounding. Numerical performance gates are enabled only when ADR-007 has an
owner-approved baseline, tolerance, confidence-interval/minimum-sample rule and
abstention policy.

### 4. Independent review and separation of duties

- A workstream owner may produce artefacts but cannot be the sole reviewer for
  a gate that validates their own model, agent contract, policy/action control,
  infrastructure control or release evidence.
- Fizz independently reviews assurance evidence and makes a recommendation;
  this is not final release authority.
- Ozzy is the sole final merge/release authority until the owner approves a
  different governance model. Protected `main`, CODEOWNERS, required status
  checks and protected environments must enforce—not merely document—this rule.
- Human reviewers record identity, scope, decision, evidence-manifest hash and
  timestamp. AI-authored contributions are transparently identified in PRs and
  documentation; no artificial commits, reviews or approvals are generated.

### 5. Defect severity and risk acceptance

| Severity | Release treatment |
| --- | --- |
| Critical | Immediate release stop. No exception may waive a bypass of approval, action authority, signature/instruction integrity, evidence integrity, isolation or uncertain-execution safety. |
| High | Release stop until fixed and independently re-tested, or an owner-signed, scoped, expiring exception with compensating control is present and machine-validated. |
| Medium | Tracked release risk with owner, due date and testable mitigation; requires owner acceptance to ship if unresolved. |
| Low | Backlog item; cannot conceal a failed required control. |

An exception record is a versioned, signed artefact containing finding ID,
scope/environment, rationale, compensating control, approver, created/expiry
time and revocation status. CI validates scope and expiry. An exception cannot
override an unconditional hard failure or a Critical category above.

### 6. Release outcomes

- **Not releasable:** required gate absent/failing, unverifiable manifest, or
  unresolved Critical/High condition without permitted valid exception.
- **Release candidate:** all applicable T0–T2 gates pass and Ozzy has approved
  the candidate for the stated environment.
- **Production-like evidence complete:** release candidate plus T3 operating
  proof. It remains a synthetic reference implementation, not a production or
  regulatory-compliant system.

## Required tests and evidence

- Manifest reproducibility: same pinned tuple reproduces the manifest schema,
  suite references and artefact hashes apart from permitted timestamp/build IDs.
- Negative manifest tests: missing, stale, unscoped or tampered artefact fails
  the gate.
- Reviewer-independence test: gate cannot be marked approved without the
  required distinct reviewer/owner record.
- Exception tests: invalid signature, wrong scope, expired exception or attempt
  to waive a Critical/hard-fail condition is rejected.
- CI tier tests: PR does not falsely report GUI/cloud proof; release candidate
  cannot advance when its required persistent-harness result is absent.
- Audit cross-check: release manifest and `audit_events` agree on build,
  evidence hash and release decision.

## Consequences

- CI remains practical on branches while retaining genuine release-candidate
  tests for GUI automation and operating proof.
- Gate language becomes measurable: claims are scoped to the tier and evidence
  actually completed.
- Implementation needs protected GitHub branches/environments and a durable
  exception-record contract, both owned by the governance/platform ADRs.

## Owner decisions required

1. Approve the severity taxonomy and whether any High exception is permitted.
2. Approve final release authority and minimum independent reviewer roles.
3. Approve initial LLM/citation/abstention metrics, sample sizes and tolerances
   after ADR-007 proposes the baselines.
4. Approve which environment/harness is required for T2 and the duration/SLO
   criteria for T3 operating proof.
