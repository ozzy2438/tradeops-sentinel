---
title: "ADR-009 — GitHub Repository and Engineering Governance"
tags: [tradeops-sentinel, adr, github, governance, delivery]
status: proposed
created: 2026-07-31
---

# ADR-009 — GitHub Repository and Engineering Governance

## Status

Proposed. Requires owner approval before repository creation. This ADR
formalizes the decision already detailed and endorsed in the Architecture
Review Report (`OUTBOX/TRADEOPS_SENTINEL_ARCHITECTURE_REVIEW_REPORT.md` §9)
from `PLANS/TRADEOPS_SENTINEL_GITHUB_CICD_PROPOSAL.md`; that document remains
the full-detail reference, this ADR is the recorded decision.

## Context

The owner requires a professional GitHub-based engineering workflow with
transparent AI-agent contribution attribution and owner-retained release
authority, decided before any repository is created (per the owner's explicit
gate: no repo, code, cloud, paid resource, UiPath execution, training, or
deployment until this phase's ADRs and Release Charter are approved).

## Decision

### Repository

- **Name:** `tradeops-sentinel`.
- **Structure:** single **monorepo**. The system is one deployable product
  (ADR-008) with tightly coupled cross-workstream contracts (a canonical-model
  change in Honey's ADR-001 touches Bumble's DDL and Scout's feature
  contracts in the same PR most of the time); splitting repos at MVP stage
  would force cross-repo version pinning for a five-person team with no
  independent release cadence. Revisit only if a component needs an
  independent release cycle.
- **Visibility:** **private** during build-out; flip to public as its own
  deliberate, reviewed decision at portfolio-release time, not a default.

### Branch and review policy

- Trunk-based, short-lived feature branches (`area/short-description`).
- **Protected `main`:** no direct pushes, including from the owner or any
  agent; linear history (squash/rebase merge only).
- **CODEOWNERS**, path-scoped to the ownership map already established across
  this project's ADRs:
  ```
  /apps/agent/            @honey-handle
  /apps/ingestion/        @bumble-handle
  /apps/reconciliation/   @honey-handle @bumble-handle
  /ml/                    @scout-handle
  /infra/                 @bumble-handle
  /.github/workflows/     @bumble-handle
  /tests/assurance/       @fizz-handle
  /docs/adr/              @orchestrator-handle @ozzy-handle
  ```
  (Handles are placeholders for the GitHub identities the owner assigns.)
- **Required reviewers:** at least one CODEOWNER per touched path; **two**
  independent reviewers on any path touching the signed-action instruction,
  maker-checker routing, tool registry, or `infra/` — the highest-risk
  surfaces per the Architecture Review's Critical/High findings.
- **PR template:** linked issue, summary of what/why, checklist ("tests
  added," "ADR needed?," "touches signed-action or DDL path?"), and a
  mandatory **AI-assisted disclosure field** (see "AI-contribution
  transparency" below).
- **Issue templates:** `bug_report.md`, `feature_request.md`,
  `adr_proposal.md`, `assurance_finding.md` (so a Fizz assurance finding
  becomes a trackable work item, not only a markdown report).
- **Labels:** `area:ingestion|reconciliation|agent|ml|rag|rpa|infra|ci|assurance`,
  `severity:critical|high|medium|low` (mirrors the Architecture Review's
  severity taxonomy), `type:bug|feature|adr|spike`,
  `status:blocked|needs-owner-decision`.
- **Milestones:** one per delivery phase (Sprint 1's six epics first, then
  subsequent sprints), so issues/PRs/evidence all roll up consistently.

### Versioning and release

- SemVer for the application (`v0.1.0` pre-MVP). Tags are the only path to a
  release candidate (`vX.Y.Z-rc.N`) or a release (`vX.Y.Z`); a tag is only
  created from `main` after the ADR-010 §3 release-candidate gate passes.
- `CHANGELOG.md` generated from Conventional Commits via an automated PR
  (release-please style), not hand-maintained, to avoid drift.
- ADRs live in `docs/adr/` (MADR-style: context/decision/consequences),
  referenced from the PR that implements them — this is how every "owner
  decision required" item across the project's ADRs stays traceable to an
  actual commit.

### GitHub Projects board and traceability

- One Projects board, columns matching workflow states (Backlog → In
  Progress → In Review → Blocked/Needs Owner Decision → Done).
- Every PR references the issue it closes; every issue that originates from
  an ADR or an assurance finding references that document/finding ID — a
  chain from requirement → issue → PR → test → evidence artefact → release
  tag, without a separate tracking tool.

### AI-contribution transparency (owner's explicit requirement)

- Commit and PR authorship reflects who/what actually did the work. Where an
  AI agent (Honey/Bumble/Fizz/Scout/Orchestrator) authored a commit or PR,
  the commit trailer and PR description say so explicitly — a `Generated-by:`
  trailer alongside this workspace's existing `Signed-off-by`/`Co-authored-by`
  human-identity convention.
- **No synthetic commit history and no fabricated review** — GitHub history
  must represent real engineering work, per the owner's explicit instruction.
- **Owner-only merge and release authority**, enforced structurally, not by
  stated intention: CODEOWNERS requires a human reviewer on every path (an
  agent can open a PR but cannot be the approving CODEOWNER for `main`);
  protected-environment rules (ADR-010) require a named human approval before
  any deploy to `staging` or the production-like demo environment; only the
  owner (and any explicitly delegated human) holds repository admin —
  contributors, including agents' associated accounts, get **write**, not
  **admin**.

### Least privilege and separation of duties

- Contributors get write access scoped to the repo, not admin.
- CI authenticates via the built-in `GITHUB_TOKEN` scoped per-workflow, and to
  AWS (optional cloud path) via OIDC — never static long-lived keys stored as
  repo secrets where avoidable.
- PR author cannot approve their own PR (GitHub default, enforced).
- Protected-environment reviewers for `staging`/production-like must be
  different individuals from whoever authored the triggering merge.

## Consequences

- A five-person (agent) team gets a governance model sized to its actual
  cadence — monorepo, trunk-based, path-scoped review — without the overhead
  multi-repo or gitflow-style branching would add at this scale.
- The AI-authorship trailer convention makes the "described transparently"
  requirement mechanically checkable (grep the git log) rather than a
  one-time promise.
- Structural enforcement (CODEOWNERS + protected environments) means owner
  authority over merges/releases survives even if a future session forgets
  the stated intention — it's a repository setting, not a norm.

## Owner decisions required

1. GitHub org/account location for `tradeops-sentinel`.
2. Confirm private visibility during build-out and who decides the later
   flip to public.
3. Confirm the exact `Generated-by:`/`Signed-off-by`/`Co-authored-by` trailer
   format — this workspace's existing convention (`AGENTS.md`) already
   requires human `Signed-off-by`/`Co-authored-by`; this ADR proposes adding
   `Generated-by:` for the authoring agent, placed before those trailers.
4. Confirm which human GitHub identities map to each CODEOWNERS path
   (currently placeholders).

## Related ADRs

ADR-010 (CI/CD pipeline that runs against this repository's branch
protection and environments), ADR-008 (runtime whose code lives in this
repository's structure).
