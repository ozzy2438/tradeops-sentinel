---
title: "ADR-011 — Legacy Automation Runtime and Cost Decision"
tags: [tradeops-sentinel, adr, uipath, rpa, cost]
status: draft
created: 2026-07-31
---

# ADR-011 — Legacy Automation Runtime and Cost Decision

## Status

Proposed. Requires owner approval before implementation or any paid-resource
activation.

## Context

The prior infra proposal asserted an unattended-UiPath monthly price from a
third-party source and treated it as a budget input. Scout's independent
review (SB P-09) found this **not decision-grade**: UiPath's own pricing page
directs Standard/Enterprise licensing to sales, and per-Robot-Unit or
per-minute Serverless consumption pricing is not publicly disclosed. I
re-verified this directly today (2026-07-31) rather than relying on the prior
citation:

- UiPath Community Edition's free tier covers **attended** robots only, for
  individuals/small teams/non-profits — it does not include unattended
  execution.
- **Unattended** Robots and **Serverless** Robots are both licensed through
  UiPath's Unified (credit/Platform-Unit) or Flex pricing frameworks; neither
  publishes a per-unit price — both require a vendor quote or sales
  conversation. ([UiPath pricing](https://www.uipath.com/pricing),
  [Orchestrator — Machine Sizes and Costs](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/machine-sizes-and-costs),
  [Orchestrator — Unified Pricing licensing](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/unified-pricing-cloud-robots))
- Serverless Robots consume Platform Units billed per minute by machine size
  and environment type — the *mechanism* is documented, the *price* is not.

This ADR retracts the prior "~$420/mo" figure as a budget input: it was
sourced from a third-party pricing-explainer site, not UiPath directly, and
per the review's binding principle, **no unverifiable cost claim may inform a
budget decision.**

## Decision

### 1. Compare the four options on what's actually knowable today

| Option | Cost basis | MVP-suitability |
| --- | --- | --- |
| Attended UiPath | Free (Community) | Not usable — MVP requires unattended/headless execution against the mock legacy app; attended requires a human to trigger each run. |
| Unattended UiPath | Quote-based (Unified/Flex), not public | Not decision-grade until a vendor quote exists. Also needs a licensed Orchestrator + a Robot host (Windows), adding hosting/network design (egress/proxy — see §3) before any execution. |
| UiPath Serverless Robots | Consumption-based (Platform Units/minute), rate not public | Same blocker — mechanism is documented, price is not. Lower operational footprint than a dedicated Robot VM if a quote is obtained. |
| **Deterministic local RPA substitute** | **Free, open source** | **Selected for MVP** — see §2. |

### 2. MVP executor: a deterministic local RPA substitute, not UiPath

For the MVP vertical slice, the executor behind the Action Gateway (ADR-008
§3) is **Playwright-driven browser automation** against the mock legacy
booking application (built as a small web app specifically so this is
possible). Rationale:

- It exercises the *actual risk the architecture review is worried about* —
  a UI-only write surface with no atomic API, requiring read-back-before-write
  and read-back-verification (ADR-005, ADR-008 §3) — without needing a
  licensed product or a Windows host.
- It is free, scriptable, and **more deterministic in CI than a real UiPath
  Robot would be at this stage**, because the mock app's markup/selectors are
  fully controlled by us; this directly resolves the CI-flakiness Critical
  finding from my own review of Fizz's plan (`PLANS/TRADEOPS_REVIEW_BUMBLE_ON_FIZZ.md`,
  finding 2) for the MVP milestone specifically — headless Chromium runs on
  ordinary Linux CI runners, no persistent Windows harness is required to
  prove the *safety pattern* (only to prove *UiPath-specifically*, which is a
  separate, later claim).
- It preserves the executor boundary as an **interface**, not a vendor
  dependency: the Action Gateway hands off a signed work item and receives a
  typed read-back result. Swapping Playwright for a real UiPath Robot later is
  a swap behind that interface, not an architecture change.

### 3. What UiPath integration requires when/if activated post-MVP

Not designed in detail here (out of MVP scope), but the boundary this ADR
commits to preserving: the Robot receives **no AWS or database credential**
(ADR-008 §3); network egress from any Robot host to UiPath Automation Cloud
needs an explicit allow-listed outbound path or forward proxy (Scout's review,
SB P-10 — the prior "isolated subnet, no route beyond the queue" claim was
inconsistent with an unattended Robot's need to reach Automation Cloud, and is
corrected here); Orchestrator hosting (UiPath Cloud vs. self-hosted) and
Robot-host sizing are decided only after a quote exists.

### 4. Portfolio release framing

The portfolio/demo release **does not require** a paid UiPath activation to
be credible: the Playwright-based executor demonstrates the full signed
instruction → dispatch → uncertain-execution → read-back → re-reconciliation
control loop, which is the property being evaluated. A real UiPath
integration is an **optional, clearly-labelled enhancement** the owner may
choose to fund later, once a quote makes the cost decision-grade — it is not
a prerequisite for demonstrating the architecture's safety claims.

## Consequences

- No paid resource, licence, or UiPath execution is required to reach the
  approved MVP vertical slice.
- The persistent-Windows-harness recommendation from my CI/CD proposal
  (`PLANS/TRADEOPS_SENTINEL_GITHUB_CICD_PROPOSAL.md` §3.13) is deferred along
  with real UiPath activation — it becomes relevant only if/when the owner
  funds a UiPath quote and the team activates that path post-MVP.
- The budget ceiling decision (ADR-008 owner decision 3) is materially
  simplified: it no longer needs to account for an unverified monthly figure.
- A future reader must not assume "UiPath" is running today because the
  brief's business framing mentions it — the executor is Playwright until a
  quote and an owner decision change that, and any demo material must say so.

## Owner decisions required

1. Confirm the Playwright-based deterministic substitute as the MVP executor
   (recommended) rather than blocking Sprint 1 on a UiPath quote.
2. Whether to pursue a UiPath vendor quote at all for a later, optional
   enhancement phase — and if so, who requests it and on what timeline.
3. If/when a quote exists: Orchestrator hosting model (Cloud vs. self-hosted)
   and Robot-host network/egress design (§3), as their own follow-up decision
   — not required for MVP sign-off.

## Related ADRs

ADR-008 §3 (Action Gateway boundary this executor sits behind), ADR-005
(Honey — signed instruction contract the executor verifies before acting),
ADR-013 (Fizz — failure/uncertain-execution tests, several of which are far
easier to make deterministic against a Playwright-controlled mock app than
against a real UiPath Robot).

## Sources

- [UiPath Plans and Pricing](https://www.uipath.com/pricing)
- [UiPath Orchestrator — Machine Sizes and Costs](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/machine-sizes-and-costs)
- [UiPath Orchestrator — Unified Pricing licensing](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/unified-pricing-cloud-robots)
- [UiPath Orchestrator — Executing unattended automations with Serverless robots](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/executing-unattended-automations-with-serverless-robots)
- [UiPath Robot proxy guidance](https://docs.uipath.com/robot/standalone/2024.10/admin-guide/redirecting-robots-through-a-proxy-server-unattended)
