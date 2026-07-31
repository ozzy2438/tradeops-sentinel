# Contributing to TradeOps Sentinel

## Workflow

This repository is **trunk-based**: `main` is the single protected integration branch.

- No direct commits to `main` — this includes the repository owner.
- One short-lived feature branch per issue: `area/short-description` (e.g. `reconciliation/ssi-tolerance-fix`).
- Every change is a pull request, linked to an issue, using the PR template.
- Required status checks must pass before merge (see `.github/workflows/`).
- CODEOWNERS review is required; see `.github/CODEOWNERS` for path ownership.
- Squash or rebase merge only — linear history, no merge commits.

## Architecture Decision Records (ADRs)

Every architecturally significant decision is recorded in [`docs/adr/`](docs/adr/) (MADR style: context / decision / consequences) **before** the implementation that depends on it lands. If your change touches a decision that isn't covered by an existing ADR, propose one using the `adr_proposal` issue template before opening the implementation PR.

## AI-contribution transparency (required)

This project is built collaboratively by a human owner and AI agents. Per the owner's explicit requirement, **AI-assisted contributions must be disclosed transparently, and GitHub history must represent real engineering work** — no synthetic commits, no fabricated reviews, no misleading activity.

Every commit authored with AI assistance carries a `Generated-by:` trailer identifying the authoring agent, in addition to the human operator's trailers:

```
Generated-by: <agent-name> (e.g. Bumble, Honey, Fizz, Scout, Orchestrator)
Co-authored-by: Osman Orka <REPLACE_WITH_REAL_EMAIL>
Signed-off-by: Osman Orka <REPLACE_WITH_REAL_EMAIL>
```

Order matters: `Generated-by:` first, then `Co-authored-by:`, then `Signed-off-by:`, with one blank line separating the trailer block from the commit body. A CI check (`scripts/check_ai_trailer.py`) enforces the presence of the correct trailers on every commit in a pull request.

> **Note:** the placeholder emails above must be replaced with the real, verified email of the human operator before any commit is made — see the open question raised in this repository's setup thread. Do not commit with a guessed or example.com-style address.

Regardless of who or what authored a change, **the repository owner retains sole authority over merges to `main` and release/tag creation.** This is enforced structurally via branch protection and CODEOWNERS, not left as a stated policy.

## Pull request requirements

- Linked issue.
- Clear "what and why" in the description.
- Checklist: tests added, ADR needed?, touches signed-action/DDL path?
- AI-assisted disclosure field completed.
- All required status checks green — **a disabled or placeholder gate is never reported as passing evidence** (per the MVP Release Charter's explicit prohibition on unverifiable claims).

## Commit and code style

- Python: `ruff` for lint/format, `mypy` for type checking (see `pyproject.toml`).
- Conventional Commits for commit subject lines (`feat:`, `fix:`, `docs:`, `chore:`, etc.) — `CHANGELOG.md` is generated from these.

## Definition of Ready / Definition of Done

Every issue must satisfy the Definition of Ready before work starts and the Definition of Done before it's closed — see the MVP Release Charter §30/§31 (referenced from `docs/adr/README.md`).
