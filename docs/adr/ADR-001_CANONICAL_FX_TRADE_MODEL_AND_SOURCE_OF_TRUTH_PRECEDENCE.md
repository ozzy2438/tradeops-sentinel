---
title: "ADR-001 — Canonical FX Trade Model and Source-of-Truth Precedence"
tags: [tradeops-sentinel, adr, canonical-model, source-of-truth, reconciliation]
status: draft
created: 2026-07-31
---

# ADR-001 — Canonical FX Trade Model and Source-of-Truth Precedence

## Status

Proposed. Requires owner approval before implementation. This ADR supplies the
logical contract consumed by ADR-002, ADR-006 and ADR-008.

## Context

The MVP receives synthetic FIX-style execution/trade-capture events, FpML-style
confirmations, mock legacy-booking observations and regulatory extracts. These
sources can disagree, arrive late or be replayed. A canonical trade state is a
versioned projection for reconciliation; it must not silently become a new
authority or erase source history.

The architecture review also found ambiguity in trade linkage, case grain and
FX arithmetic. Without explicit field precedence and observation lineage, the
same inputs could produce different breaks or an action could be approved
against stale evidence.

## Decision

### 1. MVP scope and grain

- The MVP operates one synthetic tenant with multiple portfolios.
- One canonical trade belongs to exactly one tenant and portfolio.
- One exception case refers to exactly one canonical trade and portfolio. It
  may contain multiple breaks for that trade only.
- Cross-trade/netting cases, multi-tenant administration and portfolio-spanning
  approvals are post-MVP.

### 2. Logical entities

| Entity | Purpose and grain |
| --- | --- |
| `source_observations` | Append-only envelope for one received source record/version, its raw-artifact hash, schema result and ingestion lineage. |
| `execution_observations` | Normalised FIX-style execution/trade-capture view, one version per source execution event. |
| `confirmation_observations` | Normalised FpML-style confirmation view, one version per confirmation record. |
| `booking_observations` | Read-only snapshot of one mock legacy booking record/version. |
| `regulatory_extract_observations` | Read-only snapshot of one reporting extract row/version. |
| `trade_linkage_candidates` | Deterministic candidate links with rule/version, match evidence and score components; never authority by itself. |
| `linkage_decisions` | Versioned accepted/rejected/ambiguous linkage decision, resolver identity, reason and supersession chain. |
| `canonical_trades` | Stable trade identity and portfolio scope; contains no mutable source evidence. |
| `canonical_trade_state_versions` | Immutable derived projection for one trade at one reconciliation input watermark. |
| `reconciliation_runs` | Rule-set version, source-version set, canonical-state version and deterministic result. |
| `trade_breaks` | Versioned symptom detected by ADR-002; references exact source and canonical evidence. |
| `exception_cases` | Workflow container for one trade/portfolio and its breaks. |
| `evidence_items` | Versioned, scoped references to source, runbook, decision and action evidence. |
| `case_evidence_snapshots` | Frozen set of evidence versions supplied to policy, the LLM or human review. |
| `feature_snapshots` | Post-MVP ML contract: one model/version/time-bound feature vector tied to a canonical-state version; no MVP model depends on it. |

Every material entity carries a deterministic ID, schema version,
`tenant_id`, `portfolio_id`, `correlation_id`, source or causal reference,
created time, actor/system identity and supersession/version metadata. Mutable
business facts are represented by new versions, not destructive updates.

### 3. Canonical FX representation

For FX Spot and FX Forward the canonical representation records:

- product type (`FX_SPOT` or `FX_FORWARD`);
- trade and source identifiers;
- base and terms currency as uppercase ISO-style three-letter codes;
- side expressed relative to the base currency;
- base amount and terms amount as decimal values with explicit scale;
- quoted rate with explicit orientation `terms_currency_per_base_currency`;
- trade date, value date and lifecycle status;
- counterparty and book/portfolio references as synthetic scoped identifiers;
- the source observation and field-resolution rule that supplied every field.

Binary floating-point is prohibited for economic comparison. Parsing and
comparison use decimal arithmetic with declared source precision. Currency-pair
orientation and side are normalised once by a versioned deterministic mapping;
the original source values remain available as evidence.

### 4. Source-of-truth matrix

| Field or fact | Trusted source | Secondary evidence | Canonical rule |
| --- | --- | --- | --- |
| Execution existence, execution ID, executed product and economics | FIX-style execution/trade-capture | Confirmation and booking | Execution is authoritative; disagreement is a break, never a silent overwrite. |
| Confirmation existence, status and confirmed terms | FpML-style confirmation | Execution and booking | Confirmation is authoritative for its own status/content; disagreement with execution is a break. |
| Current legacy-booking values and record version | Fresh booking read-back | Earlier booking observations | Latest successfully validated read-back is authoritative only for what the mock application currently stores. |
| Portfolio/book assignment | Execution/trade-capture unless an owner-approved correction decision exists | Booking | Precedence is versioned; a human correction decision may supersede the projection but never rewrites the source. |
| Regulatory extract content | Regulatory extract | Canonical projection and booking | Extract is authoritative for what was reported, not for what the trade ought to be. |
| Link between observations | Versioned deterministic linkage rule or governed human linkage decision | Source identifiers | Ambiguous linkage cannot populate authoritative canonical fields. |
| Break fact and status | Deterministic reconciliation service | Source/canonical evidence | Only the rule version that evaluated an exact source-version set creates or resolves a break. |
| Action outcome | Fresh booking read-back plus post-action reconciliation | Robot receipt | Robot success/receipt alone is never evidence that the action applied correctly. |

The canonical projection records the selected value, originating source/version
and rule/version for each field. It does not merge conflicting values by
majority vote and an LLM cannot select source precedence.

### 5. Observation history, linkage and versioning

- Ingestion is at-least-once. A source-unique version or deterministic
  fingerprint deduplicates semantic observations while recording duplicate
  delivery evidence.
- Late or corrected records create new observations and a new canonical-state
  version. Prior projections and reconciliation results remain addressable.
- Automatic linkage is limited to exact, deterministic MVP keys approved in
  the source contract. Zero or multiple candidates create a linkage break.
- Human linkage resolution records the candidates, chosen link, evidence,
  maker identity, reason, scope and superseded decision. It cannot bridge
  tenants or portfolios.
- Policy, LLM, review and action contracts bind to exact canonical,
  reconciliation and evidence-snapshot versions. A newer material observation
  supersedes pending authority and triggers fresh reconciliation.

### 6. Reconciliation ownership

The deterministic reconciliation service alone:

1. resolves canonical fields according to this ADR;
2. invokes the versioned rules in ADR-002;
3. records reconciliation and break versions; and
4. declares whether post-action state matches the approved target.

The LLM may explain a recorded mismatch but cannot create, resolve or change a
break fact, linkage decision or canonical field.

## Required tests and evidence

- Contract fixtures prove the same ordered or replayed inputs yield the same
  canonical state and reconciliation result.
- Currency orientation, side and decimal-scale fixtures cover both Spot and
  Forward.
- Late-arrival and correction tests preserve prior versions and supersede stale
  case/action snapshots.
- Ambiguous, unmatched, duplicate and cross-portfolio linkage tests fail closed.
- Provenance tests trace every canonical field and break to exact source
  observation, schema and rule versions.
- A fresh read-back plus reconciliation, not an RPA receipt, is required to
  prove action outcome.

## Consequences

- Source disagreements remain visible and auditable instead of being hidden in
  a mutable golden record.
- Version-bound evidence makes approvals reproducible but increases storage and
  contract discipline.
- The one-trade/one-portfolio case invariant simplifies authority and isolation.
- `feature_snapshots` define a future seam only; learned models remain post-MVP.

## Owner decisions required

1. Approve one synthetic tenant with multiple portfolios and one case per trade.
2. Approve the execution source as authority for executed economics and the
   FpML-style record as authority for confirmation content/status.
3. Approve the exact deterministic linkage keys and the human linkage role.
4. Approve decimal scales and any source-specific normalisation policy before
   reconciliation tolerances are implemented.

## Review findings closed

Closes Honey-on-Scout H-02 and M-04, Fizz-on-Honey M-03/M-04/M-07, and the
Architecture Review source-precedence, scoring-grain and feature-snapshot
findings.
