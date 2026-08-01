---
title: "ADR-002 — Deterministic Trade-Break Taxonomy"
tags: [tradeops-sentinel, adr, reconciliation, trade-breaks, fx]
status: draft
created: 2026-07-31
---

# ADR-002 — Deterministic Trade-Break Taxonomy

## Status

Accepted for the TS-4 contract implementation under issue #4. Depends on
ADR-001. Owner approval is recorded in the TS-4 execution assignment; this
decision remains limited to the deterministic contract slice and does not
authorize persistence, reconciliation execution, UI, LLM, model, or cloud
work.

## Context

The MVP needs a bounded, explainable set of breaks that can be detected and
resolved without ML or LLM judgement. A break describes an observed lifecycle
symptom. It is not a root-cause label: delayed confirmation, parser defects or
operator error may explain a break later, but must not change whether the
deterministic invariant failed.

## Decision

### 1. Break record contract

Every break records:

- deterministic `break_id`, taxonomy and rule versions;
- tenant, portfolio, trade and reconciliation-run IDs;
- exact source/canonical versions and field paths evaluated;
- invariant, expected value, observed value and configured tolerance;
- severity and deterministic priority inputs;
- lifecycle state, detected/resolved timestamps and resolution evidence;
- optional human disposition and separately governed causal-label reference.

No free-text or model output determines existence, severity or closure.

### 2. Exact MVP break families

| Code | Detection invariant | Default severity | Required evidence | Resolution condition |
| --- | --- | --- | --- | --- |
| `MISSING_REQUIRED_SOURCE` | A required execution, confirmation or booking observation is absent after its configured deterministic arrival window. | High for execution/booking; Medium for confirmation until owner sets materiality | Ingestion watermark, expected source, lifecycle timestamp | A valid source observation arrives and a new reconciliation passes, or an authorised non-action disposition closes the expectation with reason. |
| `AMBIGUOUS_OR_UNMATCHED_LINKAGE` | An observation has zero or multiple eligible canonical-trade matches, or a proposed link crosses tenant/portfolio scope. | High | Candidate links, identifiers, rule version | One governed linkage decision produces exactly one in-scope link and a fresh reconciliation passes. |
| `DUPLICATE_SOURCE_CONFLICT` | The same source business key/version has non-identical content, or two active records claim the same unique lifecycle identity. | High | Both payload hashes and source metadata | Source correction/supersession is received or an authorised linkage disposition leaves one active version, followed by reconciliation. |
| `CURRENCY_PAIR_OR_SIDE_MISMATCH` | Normalised base/terms currencies or base-relative side disagree across required sources. | Critical | Original and normalised fields plus mapping version | Sources agree under the approved mapping after correction and reconciliation. |
| `ECONOMIC_VALUE_MISMATCH` | Base amount, terms amount or quoted rate differs outside the approved field tolerance. | Critical | Decimal values, scales, rate orientation and tolerance version | Fresh observations are within tolerance and reconciliation passes. |
| `TRADE_OR_VALUE_DATE_MISMATCH` | Trade date or value date differs across required sources after explicit calendar/normalisation rules. | High | Original dates, time zone/calendar version | Dates agree after correction and reconciliation. |
| `LIFECYCLE_STATUS_MISMATCH` | Confirmation, booking or reporting lifecycle status is inconsistent with the allowed state relation for the canonical trade. | High | Source statuses and relation-rule version | Allowed status relation holds on fresh reconciliation. |
| `POST_ACTION_VERIFICATION_FAILURE` | Read-back is unavailable, target value/version differs, an unapproved field changed, or the original applicable break remains after action. | Critical | Instruction, pre-read, post-read, changed-field diff and reconciliation | Never auto-resolved from a robot receipt; requires successful verified read-back/reconciliation or human escalation/disposition. |

`BOOKING_VERSION_CONFLICT` is represented as a safety state in ADR-005/ADR-013,
not as permission to overwrite. It may additionally create
`POST_ACTION_VERIFICATION_FAILURE` evidence if discovered after dispatch.

### 3. Comparison and tolerance policy

- Identifiers, currency codes, side, product type and lifecycle enums use exact
  comparison after versioned normalisation.
- Amounts and rates use decimal comparison only. The rule records absolute
  and/or relative tolerance by field, currency pair and product where approved.
- Dates use exact normalised dates. The MVP does not infer holidays, cut-offs or
  expected Spot/Forward tenors unless the owner approves a versioned calendar
  rule and fixtures.
- Missing-source windows and numeric tolerances are configuration with version,
  owner approval and effective dates; they are not model parameters.
- Until tolerances are approved, tests may use labelled fixture values but the
  system cannot claim operationally meaningful thresholds.

### 4. Severity, materiality and priority

Severity represents the potential operational consequence of the symptom.
Materiality governs the human-control route and is a separate deterministic
policy decision. MVP case priority is a deterministic tuple, for example:

`materiality band → break severity → lifecycle deadline → case age`.

Learned priority scoring is post-MVP. The LLM cannot alter severity,
materiality, priority or the required approver route.

### 5. Lifecycle

Permitted break states are:

`OPEN → UNDER_INVESTIGATION → RESOLUTION_PROPOSED → ACTION_PENDING |
NO_ACTION_DISPOSITION_PENDING → VERIFYING → RESOLVED | ESCALATED`.

`RESOLVED` requires a new reconciliation run whose source-version set proves
the invariant passes, except for an owner-approved non-action disposition.
Reopening creates a new break version linked to the prior break. Deletion and
silent mutation are prohibited.

### 6. Causal-label boundary

Root-cause hypotheses and future ML labels are separate records referencing a
break version. They may express `UNKNOWN` or abstain. They never create,
prioritise, resolve or rewrite a break. Post-MVP classifier evaluation must use
the independent scenario-truth ledger in ADR-006, not symptom codes as causal
ground truth.

## Required tests and evidence

- At least one positive, boundary and negative fixture per rule for Spot and
  Forward where applicable.
- Decimal orientation/rounding tests and explicit tolerance-boundary tests.
- Replay, late-arrival and corrected-source tests prove deterministic reopening
  and resolution.
- Cross-portfolio and ambiguous-link tests cannot produce an actionable case.
- `POST_ACTION_VERIFICATION_FAILURE` tests cover unexpected-field changes and
  persistent original breaks.
- Mutation tests show LLM text and causal labels cannot change rule results.

## TS-4 implementation traceability

- Issue: [#4 — Deterministic Trade-Break Taxonomy and Lifecycle Contracts](https://github.com/ozzy2438/tradeops-sentinel/issues/4)
- Models: `packages/contracts/models.py` (`BreakTaxonomy`, `TradeBreak`)
- Schemas: `packages/contracts/schemas/break-taxonomy.schema.json` and
  `packages/contracts/schemas/trade-break.schema.json`
- Fixtures: `packages/contracts/examples/valid/` and
  `packages/contracts/examples/invalid/`, registered in
  `packages/contracts/examples/manifest.json`
- Tests: `tests/test_break_contracts.py` and the manifest-driven contract tests
- Review PR: [#20 — TS-4 deterministic trade-break taxonomy and lifecycle contracts](https://github.com/ozzy2438/tradeops-sentinel/pull/20)

## Consequences

- The MVP demonstrates a small rule set deeply instead of a broad, unvalidated
  taxonomy.
- Several operational values remain owner-approved configuration, which is
  honest but blocks threshold claims until decided.
- Root-cause ML can evolve independently without contaminating reconciliation
  truth.

## Owner decisions required

1. Approve the eight MVP break families and default severities.
2. Approve required-source arrival windows.
3. Approve decimal scales and amount/rate tolerances by field/product.
4. Approve materiality bands, priority ordering and authorised non-action
   dispositions.
5. Approve whether any market-calendar rule is in MVP; otherwise dates remain
   exact source comparisons only.

## Review findings closed

Closes Honey-on-Scout H-01/H-04 and the Architecture Review requirement for a
bounded deterministic taxonomy with an explicit symptom-versus-cause seam.
