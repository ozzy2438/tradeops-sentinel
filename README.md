# TradeOps Sentinel

**Global Markets Trade-Break Automation & Regulatory Evidence Platform — reference implementation.**

> **Status: Sprint 1 — foundations only.** This repository currently contains repository/engineering governance and CI scaffolding (Epic E1). No domain code, no LLM workflow, no cloud deployment, and no RPA executor implementation exist yet.

## What this is

A production-like reference implementation demonstrating how deterministic reconciliation, one constrained/advisory LLM, human-controlled maker-checker approval, a signed/idempotent action instruction, and read-back-verified legacy-system remediation can operate together for **synthetic** FX Spot and FX Forward post-trade exception handling.

This is **not** an autonomous trading system. AI investigates, classifies, and recommends — it never approves, signs, dispatches, or executes a material change on its own. See [`docs/adr/`](docs/adr/) for the full architecture decision record set and the [MVP Release Charter](docs/CHARTER_REFERENCE.md) for the approved scope.

## Claims that must not be made about this project

Per the approved MVP Release Charter (§27), the following claims are **forbidden** unless a specific, tested, implemented mechanism backs them for the exact environment described:

- **"Immutable" / "WORM" / "legal-hold"** — evidence is *tamper-evident* (insert-only roles + hash-chain + independent verifier), never claimed immutable or WORM-compliant.
- **"Exactly once"** — actions are *at-most-once semantic* with read-back verification, never claimed exactly-once.
- **"Secure" / "production-ready"** — without naming the specific control and its test.
- **"Regulatory-compliant"** — this is a portfolio/reference implementation using synthetic data only; it does not claim regulatory compliance.
- Any UiPath or cloud-deployment result that was not actually run. The MVP legacy executor is **Playwright**, not UiPath — see ADR-011. Any demo material must say so explicitly.

## Scope boundaries (MVP)

- Synthetic FX Spot/Forward data only — **no real bank, customer, or market-sensitive data**, ever.
- One non-economic automated action (`SET_CONFIRMATION_REFERENCE`); all other trade-economic changes are manual-only in the MVP.
- One synthetic tenant, at least two portfolios (isolation is tested, not just asserted).
- No live market connectivity, order generation, price prediction, or trading strategy — see ADR context in `docs/adr/`.

## Repository structure

```
apps/app/              # single modular application runtime (ADR-008)
packages/contracts/    # canonical model + schemas (ADR-001/002/005) — Epic E2
packages/generator/    # deterministic synthetic FX generator (ADR-006) — Epic E3
packages/reconciliation/ # deterministic reconciliation engine (ADR-002) — Epic E5
packages/evidence/     # hash-chain audit + evidence_verifier (ADR-012) — Epic E6
packages/executor/     # Playwright-based legacy executor (ADR-011) — post-Sprint-1
docs/adr/              # all 14 Architecture Decision Records + index
infra/                 # Terraform for the optional cloud reference path (ADR-008 §22) — not used in Sprint 1
tests/                 # cross-package contract/integration tests
scripts/               # CI helper scripts (e.g. AI-authorship trailer check)
src/tradeops_sentinel/ # placeholder package proving the CI lint/type/unit gates run on real output
```

## Quickstart (local, no cloud, no paid resources)

Local runtime is a single `docker-compose` stack (PostgreSQL 16 + pgvector, one application container, mock legacy booking app, Playwright executor) per ADR-008. The compose file and application code land in later Sprint-1 epics (E2–E6) — this is a placeholder pending that work.

## Status labels used in this repo

Issues and PRs are labelled `status:planned`, `status:implemented`, `status:locally-tested`, or `status:cloud-deployed` / `status:operationally-validated` where applicable, per the charter's requirement to distinguish planned from proven work at every stage.

## Governance

- Trunk-based development, protected `main`, CODEOWNERS-enforced review — see [CONTRIBUTING.md](CONTRIBUTING.md).
- Every architecturally significant decision is recorded as an ADR in `docs/adr/` before implementation.
- AI-assisted contributions are disclosed transparently in commit trailers and PR descriptions — see [CONTRIBUTING.md](CONTRIBUTING.md). The repository owner retains sole merge and release authority, enforced structurally (not just as policy).

## License

See [LICENSE](LICENSE).
