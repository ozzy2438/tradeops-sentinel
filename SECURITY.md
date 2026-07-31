# Security Policy

## Scope

TradeOps Sentinel is a **synthetic-data-only** reference implementation. There is no real customer, bank, or market-sensitive data in this repository or any environment it deploys to during the MVP. There are no real secrets, credentials, or production systems to compromise — the "legacy booking application" this project automates against is itself a mock built for this project.

That said, the project treats its own control boundaries as security-critical, because the architecture exists to demonstrate that those boundaries hold:

- **Signed action instructions and the maker-checker approval path** (ADR-005) — anyone who can forge, replay, or bypass an instruction defeats the entire point of the project.
- **The Action Gateway / legacy executor boundary** (ADR-008/011) — the executor must never receive database or cloud credentials.
- **The tamper-evident audit/evidence chain** (ADR-012) — insert-only roles and hash-chain integrity are load-bearing, not decorative.
- **Tool/agent authority boundaries** (ADR-004) — the LLM workflow is read-only and advisory; it must never gain write, signing, or dispatch capability.

## Reporting a vulnerability or a control-boundary weakness

If you find a way to defeat any of the above (forge a signature, bypass maker-checker, get the executor a credential it shouldn't have, corrupt the audit chain undetected, or get the LLM workflow to take a write action), please open a **private** report:

1. Do not open a public GitHub issue for a control-boundary bypass.
2. Contact the repository owner directly (see the GitHub profile associated with this repository) with reproduction steps.
3. Allow time for a fix and coordinated disclosure before any public write-up.

Ordinary bugs, documentation issues, and feature requests should go through normal GitHub issues using the templates in `.github/ISSUE_TEMPLATE/`.

## What this policy does not cover

Since there is no live UiPath integration, no cloud deployment, and no real data in the MVP (see `README.md` and ADR-011/ADR-008 §22), there is no live production environment to report incidents against yet. This policy will be extended once/if an optional cloud reference path or a real UiPath integration is activated (both are explicit, owner-gated, post-MVP decisions).
