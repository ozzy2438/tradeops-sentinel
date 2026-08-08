# TradeOps Sentinel

**Global Markets Trade-Break Automation & Regulatory Evidence Platform — reference implementation.**

> **Status: production-candidate FX Spot/Forward reconciliation MVP built on a
> production-oriented deterministic reconciliation reference implementation.**
> The product runs end to end: load synthetic FX data, assemble policy-enforced
> canonical trade state, run deterministic reconciliation, and inspect detected
> breaks with full provenance in a dashboard. One tightly bounded controlled-AI
> remediation flow is implemented, including an optional Azure OpenAI provider
> and one local LightGBM priority model with per-case SHAP evidence.
> TS-14 audit ledger, TS-15 release-evidence gate and the Playwright executor
> remain out of scope.

## Run the product

```bash
cp .env.example .env      # then fill in database, API-key and signing-secret values
docker compose up --build
```

| Surface | URL |
| --- | --- |
| API documentation | <http://localhost:8000/docs> |
| Dashboard | <http://localhost:8501> |

In the dashboard: **Load Synthetic Demo Data** → **Run Reconciliation** →
inspect breaks and open **Break detail** for expected/observed values and
source provenance.

![Dashboard overview](docs/screenshots/01-dashboard-overview.png)

![Break detail](docs/screenshots/02-break-detail.png)

A verbatim end-to-end transcript is in [`docs/DEMO_RECORD.md`](docs/DEMO_RECORD.md).

## Architecture

```
        ┌────────────────────┐
        │  Streamlit         │   HTTP + X-API-Key only.
        │  dashboard :8501   │   Holds no database credentials.
        └─────────┬──────────┘
                  │
        ┌─────────▼──────────┐
        │  FastAPI :8000     │   9 product + 5 remediation endpoints, API-key guard.
        └─────────┬──────────┘
                  │
        ┌─────────▼──────────────────────────────────────────┐
        │  apps/api/service.py                               │
        │  demo load → canonical assembly → reconciliation    │
        └─────────┬──────────────────────────────────────────┘
                  │  consumes the deterministic core unchanged
   ┌──────────────┼───────────────┬──────────────────┐
   │              │               │                  │
┌──▼─────────┐ ┌──▼───────────┐ ┌─▼──────────────┐ ┌─▼─────────────┐
│ generator  │ │ contracts +  │ │ persistence    │ │ reconciliation│
│ synthetic  │ │ hashing +    │ │ source-of-     │ │ engine        │
│ FX corpus  │ │ validation   │ │ truth policy   │ │ 8 families    │
└────────────┘ └──────────────┘ └───┬────────────┘ └───────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │ packages/persistence/  │
                        │ adapter.py (psycopg 3) │
                        └───────────┬────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │  Neon PostgreSQL       │
                        │  append-only tables    │
                        └────────────────────────┘
```

## Neon setup

Use an **isolated development branch** (`tradeops-dev`), never the production
branch. From the Neon console: create the branch, then copy its connection
string into `.env`:

```bash
DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
TRADEOPS_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

`.env` is git-ignored and never baked into an image. Credentials are passed to
containers as environment variables only. If a connection string is ever shown
or pasted anywhere, **reset the password in Neon and use the new one** — treat
the old value as compromised.

Migrations run automatically at API startup (`TRADEOPS_AUTO_MIGRATE=true`) and
are idempotent.

## What this is

A production-like reference implementation demonstrating how deterministic reconciliation, an explainable advisory priority model, one constrained/advisory LLM, human-controlled maker-checker approval, a signed/idempotent action instruction, and read-back-verified legacy-system remediation can operate together for **synthetic** FX Spot and FX Forward post-trade exception handling.

This is **not** an autonomous trading system. AI investigates, classifies, and recommends — it never approves, signs, dispatches, or executes a material change on its own. See [`docs/adr/`](docs/adr/) for the full architecture decision record set and the [MVP Release Charter](docs/CHARTER_REFERENCE.md) for the approved scope.

The one implemented, end-to-end demonstration of that AI/maker-checker/signed-instruction/read-back-verified loop — for exactly one break family and one field — is documented in [`docs/AI_REMEDIATION.md`](docs/AI_REMEDIATION.md).
The synthetic LightGBM training contract, immutable model tuple, validation
metrics, leakage boundary, and SHAP additivity check are documented in
[`docs/ML_PRIORITY_MODEL.md`](docs/ML_PRIORITY_MODEL.md).

### Optional live Azure OpenAI recommendation

The product remains deterministic by default. To exercise only the advisory
recommendation boundary with Azure OpenAI, without starting the database or
the legacy executor:

```bash
python -m pip install -e ".[dev,azure]"
az login
export TRADEOPS_AI_PROVIDER=azure-openai
export AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
python scripts/run_azure_recommendation_demo.py
```

The demo sends one small synthetic break, accepts only a strict structured
schema that is immediately converted to `AIRecommendation`, caps the
completion at 400 tokens, and then runs the normal deterministic policy gate.
It has no database, signing, approval, dispatch, or execution capability.
Authentication uses the current Azure CLI identity by default;
`AZURE_OPENAI_API_KEY` is supported for environments that cannot use Microsoft
Entra ID. A sanitized live-validation record is in
[`docs/AZURE_OPENAI_VALIDATION.md`](docs/AZURE_OPENAI_VALIDATION.md); see also
[`docs/AI_REMEDIATION.md`](docs/AI_REMEDIATION.md).

## Claims that must not be made about this project

Per the approved MVP Release Charter (§27), the following claims are **forbidden** unless a specific, tested, implemented mechanism backs them for the exact environment described:

- **"Immutable" / "WORM" / "legal-hold"** — evidence is *tamper-evident* (insert-only roles + hash-chain + independent verifier), never claimed immutable or WORM-compliant.
- **"Exactly once"** — actions are *at-most-once semantic* with read-back verification, never claimed exactly-once.
- **"Secure" / "production-ready"** — without naming the specific control and its test.
- **"Regulatory-compliant"** — this is a portfolio/reference implementation using synthetic data only; it does not claim regulatory compliance.
- Any UiPath or cloud-deployment result that was not actually run. The one implemented mock legacy executor (`MockLegacyBookingAdapter`, see `docs/AI_REMEDIATION.md`) is a plain Python/PostgreSQL adapter — not Playwright, and not UiPath. ADR-011 proposes a separate, still-unimplemented Playwright-driven executor for a fuller future MVP; nothing in this repository runs it today. Any demo material must say so explicitly.
- Live validation of the `anthropic` LLM provider. `AnthropicProvider` is implemented but has never been exercised against a real API call in this repository. The separate Azure OpenAI provider was exercised only through the single bounded synthetic validation recorded in `docs/AZURE_OPENAI_VALIDATION.md`; that result must not be broadened into a production or autonomous-execution claim.

## Scope boundaries (MVP)

- Synthetic FX Spot/Forward data only — **no real bank, customer, or market-sensitive data**, ever.
- One human-approved economic-field correction (`CORRECT_LEGACY_BOOKING_FIELD` on `/payload/base_amount`, exactly one break scenario), gated by mandatory Maker+Checker approval and a signed action envelope — never autonomous. See `docs/AI_REMEDIATION.md`. All other trade-economic changes remain manual/unimplemented in the MVP.
- One synthetic tenant, at least two portfolios (isolation is tested, not just asserted).
- No live market connectivity, order generation, price prediction, or trading strategy — see ADR context in `docs/adr/`.

## Repository structure

```
apps/api/              # FastAPI product service (9 product endpoints + 5 remediation endpoints)
apps/dashboard/        # Streamlit dashboard (calls the API only)
apps/app/              # planned modular application runtime seam (README only)
packages/contracts/    # canonical model + schemas (ADR-001/002/005) — Epic E2
packages/generator/    # deterministic synthetic FX generator (ADR-006) — Epic E3
packages/persistence/  # inbox semantics, SOT-enforced canonical assembly, psycopg 3 adapter + DDL
packages/reconciliation/ # deterministic reconciliation engine (ADR-002) — Epic E5
packages/remediation/  # controlled AI recommendation, policy, approvals, envelope and execution
packages/priority_model/ # versioned LightGBM inference, training contract and SHAP evidence
packages/oracle/       # independently implemented reconciliation oracle + import isolation
packages/evidence/     # planned TS-14 hash-chain/verifier seam (README only)
packages/executor/     # planned Playwright executor seam (README only)
docs/adr/              # all 14 Architecture Decision Records + index
infra/                 # Terraform for the optional cloud reference path (ADR-008 §22) — not used in Sprint 1
tests/                 # cross-package contract/integration tests
scripts/               # CI helper scripts (e.g. AI-authorship trailer check)
src/tradeops_sentinel/ # distribution package root
```

## Quickstart (local, no cloud, no paid resources)

Python 3.11 or later is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml]"
pytest -q
ruff check .
ruff format --check .
mypy
python scripts/check_wheel_install.py
```

PostgreSQL integration tests require a disposable PostgreSQL 16 database:

```bash
TRADEOPS_TEST_DATABASE_URL=postgresql://user:password@localhost/tradeops_test \
  pytest -q tests/integration
```

The wheel check builds the deployable artifact, installs it into an isolated
environment, imports every runtime package and loads the packaged source-of-
truth policy. See [production-readiness boundary](docs/PRODUCTION_READINESS.md)
for controls, branch-protection checks and remaining blockers.

## Status labels used in this repo

Issues and PRs are labelled `status:planned`, `status:implemented`, `status:locally-tested`, or `status:cloud-deployed` / `status:operationally-validated` where applicable, per the charter's requirement to distinguish planned from proven work at every stage.

## Governance

- Trunk-based development, protected `main`, CODEOWNERS-enforced review — see [CONTRIBUTING.md](CONTRIBUTING.md).
- Every architecturally significant decision is recorded as an ADR in `docs/adr/` before implementation.
- AI-assisted contributions are disclosed transparently in commit trailers and PR descriptions — see [CONTRIBUTING.md](CONTRIBUTING.md). The repository owner retains sole merge and release authority, enforced structurally (not just as policy).

## License

See [LICENSE](LICENSE).

## Known limitations

- **`POST_ACTION_VERIFICATION_FAILURE` is not surfaced by the running product.**
  The reconciliation engine supports all eight approved break families (proven
  by the unchanged TS-11 suite), but this family requires an executed action
  with pre/post read-back — the ADR-011 Playwright executor, which is out of
  scope here. The product pipeline therefore detects **seven of eight** families.
  This is stated rather than papered over.
- Linkage decisions are derived deterministically from observation content, not
  produced by a dedicated linkage engine (out of scope).
- Conflicting deliveries are quarantined in `source_event_conflicts` rather than
  processed by a full dead-letter workflow.
- No migration-version ledger; migrations are idempotent and applied in order at
  startup, but the database does not record which have run.
- Single synthetic tenant (`tenant_demo`) across two portfolios. Multi-tenant
  routing, RBAC, OAuth/JWT and user management are all out of scope.
- Concurrency: canonical version allocation is fail-closed (a race raises a
  unique violation) but has no retry helper.
- The distribution claims the generic top-level import name `packages`; a local
  `packages/` directory in the working directory will shadow it.

## Security statement

- **Synthetic demonstration data only — no real banking or customer data**, ever.
- Credentials are supplied exclusively through environment variables
  (`DATABASE_URL`, `TRADEOPS_API_KEY`). `.env` is git-ignored; no secret is
  committed, logged, or baked into an image. Secret scanning runs in CI.
- All authenticated endpoints require `X-API-Key`. A missing server-side key
  fails closed (503) rather than defaulting open.
- Error responses are typed and never expose stack traces, connection strings,
  or driver internals.
- Source ingestion is tamper-evident: every observation's content hash is
  recomputed and verified before any replay/conflict decision, so a forged hash
  cannot mask altered content.
- `source_event_inbox`, `canonical_trade_state_versions`, `reconciliation_runs`,
  `trade_breaks` and `source_event_conflicts` are append-only at the database
  boundary, guarded against `UPDATE`, `DELETE` and `TRUNCATE`.
- Containers run as non-root users.

## Project classification

**Production-candidate FX Spot/Forward reconciliation MVP built on a
production-oriented deterministic reconciliation reference implementation.**

This is not a live banking platform, and it is not connected to any real market,
customer, or settlement system.
