# Production-readiness boundary

TradeOps Sentinel is a production-oriented reference implementation using
synthetic FX data. The repository must not be described as operationally
production-ready until every item in the final section is implemented and
validated in the target environment.

## Controls implemented and continuously checked

- versioned, strict FX observation and canonical-state contracts;
- versioned canonical observation hashing, recomputed at ingress;
- policy-enforced field-level source of truth with typed fail-closed errors;
- deterministic reconciliation for the eight bounded break families;
- independent oracle with fail-closed direct, transitive and dynamic import
  isolation checks;
- append-only PostgreSQL triggers for source inbox and canonical versions;
- fresh-install, legacy-schema upgrade and full-sequence reapply migration
  tests for the append-only/portfolio-scoped DDL;
- deterministic replay/conflict and locked-input rerun invariants;
- wheel build plus clean isolated installation/import verification;
- Ruff, strict mypy, pytest, PostgreSQL 16 integration, dependency, secret and
  AI-authorship checks in pull-request CI;
- controlled-AI remediation slice for one scenario (`ECONOMIC_VALUE_MISMATCH`
  on `/payload/base_amount`): citation-backed structured recommendation,
  deterministic fail-closed policy, Maker+Checker approval, a signed/
  expiring/idempotent action envelope, mock legacy-booking execution with
  row-locked replay/timeout-recovery safety, post-action re-verification, and
  a frozen evidence record — see `docs/AI_REMEDIATION.md`.

## Manual `main` branch-protection checklist

Repository administrators should verify these settings after the new CI job
names have completed successfully at least once:

- pull requests required; direct pushes and force pushes disabled;
- required approvals and CODEOWNER review enabled for high-risk paths;
- approval of the most recent reviewable push required;
- conversation resolution required before merge;
- branch deletion disabled and linear history required;
- administrators do not have an undocumented bypass;
- the following successful status checks are required:
  `lint-type-unit`, `oracle-import-isolation`, `wheel-clean-install`,
  `postgres-integration`, `product-e2e`, `docker-build-and-smoke`,
  `ai-authorship-trailer-check`, `dependency-scan`, and `secret-scan`
  (confirmed against live branch protection on `main` at 2026-08-04);
- disabled placeholder jobs are never selected as required checks or reported
  as passing evidence.

GitHub settings are operational state rather than source code. This checklist
does not claim the live settings match it; they must be read back from GitHub
and recorded during release assurance.

## Remaining blockers to an operational production-ready claim

- implement a transactional PostgreSQL adapter and concurrent replay/conflict,
  canonical-version allocation, and rollback tests;
- complete TS-14 tamper-evident audit ledger and independent verifier;
- complete TS-15 release-evidence manifest and hard-fail completeness gate;
- validate secrets, backup/restore, observability, capacity, recovery,
  deployment and rollback controls in a named target environment;
- obtain independent human review and owner-controlled release approval.

Until those blockers close, the accurate portfolio description is
"production-oriented, deterministically tested reference implementation",
not "deployed production system" or an unqualified "production-ready".
