---
title: "ADR-006: Synthetic Data, Scenario Truth and Leakage Controls"
tags: [tradeops-sentinel, adr, synthetic-data, fx, leakage-controls, reconciliation]
status: draft
created: 2026-07-31
---

# ADR-006: Synthetic Data, Scenario Truth and Leakage Controls

**Status:** Proposed — requires owner approval before generator, reconciliation, feature, evaluation, or model work begins.

**Decision owners:** Scout (data/evaluation), Honey (canonical model, source precedence, break taxonomy), Fizz (independent assurance review), Ozzy (approval).

**Closes:** Honey-on-Scout H-03 (independent oracle), M-01/M-02/M-05/M-07, and the review-report requirements for a reproducible synthetic corpus. It consumes, but does not redefine, ADR-001 (canonical model/source precedence) and ADR-002 (deterministic break taxonomy).

**Owner decision update (2026-07-31):** the approved MVP corpus is **144 deterministic lifecycle roots**: 48 clean lifecycles and 96 controlled mutations, comprising six independently parameterised mutations for each of ADR-002's eight break families across both FX products. It uses one synthetic tenant with at least two synthetic portfolios so portfolio isolation is testable. This is approved planning scope; the corpus has not been generated yet.

## Context

TradeOps Sentinel's MVP must demonstrate a complete post-trade control loop using **synthetic FX Spot and FX Forward** data only. It must not use live market connectivity, real trade, counterparty, customer, bank, or booking data. The corpus must support deterministic reconciliation tests now and preserve a leakage-safe basis for later model work without claiming that a synthetic corpus proves production performance.

The architecture review identified four material risks:

- a generator and reconciler can share the same defect and report a false pass;
- generator metadata or later outcomes can leak into a model or LLM context;
- observation-level splitting can leak correction lineage across time; and
- synthetic allocation can reward template recognition instead of useful causal reasoning.

The MVP has no learned root-cause or priority model. Its deterministic priority policy and constrained LLM evaluation are governed elsewhere. This ADR therefore establishes data, truth, and evaluation controls; it does **not** authorise model training.

## Decision

### 1. Synthetic scope and minimum corpus

The initial release corpus is a deterministic, versioned fixture set for one synthetic tenant and at least two synthetic portfolios. It covers only `FX_SPOT` and `FX_FORWARD` using a deliberately small, owner-approved currency-pair configuration. Pair selection is test configuration, not a representation of market coverage or convention.

The owner-approved corpus contains **144 deterministic lifecycle roots**:

| Population | Minimum | Purpose |
| --- | ---: | --- |
| Clean, internally coherent Spot/Forward lifecycles | 48 | Prove that valid observations do not create a break. |
| Controlled mutation lifecycles | 96 | Exercise every ADR-002-approved MVP break family across both products. |

For each of ADR-002's eight approved break families and each product, the corpus contains six independently parameterised mutation lifecycles: `8 families × 2 products × 6 mutations = 96`. The 48 coherent lifecycles are allocated across both products and the two portfolios. A taxonomy expansion requires a new owner-approved corpus-scale decision; no scenario slots are silently reallocated.

These counts are a minimum **contract-test coverage** scale, not a sample size for statistical efficacy, calibration, or model-performance claims. They are not sufficient to train or certify a learned model.

### 2. Fixture contracts and simulated timeline

Each lifecycle root carries opaque synthetic identifiers and uses four separately versioned, schema-validated fixture contracts:

1. FIX-style execution observation;
2. FIX-style trade-capture observation;
3. FpML-style confirmation observation; and
4. read-only mock legacy-booking observation.

Execution and trade capture are distinct fixture types even when a shared transport helper is later used. The MVP pins one documented FpML-inspired Spot/Forward subset, `fpml-style-fx-v1`; it is not a full FpML implementation. The subset is informed by the common FX exchange-rate, exchanged-currency, and date components documented by [FpML](https://www.fpml.org/spec/fpml-5-6-5-rec-1/html/confirmation/fpml-5-6-intro-8.html).

Every lifecycle records distinct `event_time`, `effective_time`, and `ingest_time` in UTC, plus source sequence and source version. Its baseline observation sequence is execution → capture → booking observation → confirmation → reconciliation. Parameterised variants may deliberately introduce late arrival, revision, replay, missing observation, or out-of-order arrival. A synthetic business-calendar configuration, including value-date rules and any date tolerance, is versioned separately; the platform does not hard-code a real-market settlement convention.

The first vertical slice does not require a regulatory-reporting extract in the generator corpus. That source is explicitly **post-MVP** unless an owner-approved ADR changes the vertical slice.

### 3. Boundaries between facts, truth, policy, and action

The following data products are separate, versioned artefacts with distinct consumers:

| Artefact | Contains | May be read by | Must not be read by |
| --- | --- | --- | --- |
| Source-fixture bundle | Synthetic source observations and schema manifests | ingest/reconciliation tests | LLM as arbitrary raw-object access; any live integration |
| Canonical/reconciliation fixture | expected source links and locked observation watermarks | deterministic test harness | scenario-truth allocation logic |
| Scenario-truth ledger | latent synthetic cause, source mutation, expected deterministic difference facts, scenario family/template, seed, and provenance graph | generator/evaluator only | runtime services, LLM prompts, feature rows, RAG corpus, UI, traces |
| Policy test fixture | expected deterministic case route, approval need, and permitted action category for a locked case snapshot | policy/e2e evaluator | generator labels, model features, LLM prompt context |
| Release manifest | hashes, versions, counts, test outputs and evaluator identity | assurance/release reviewers | runtime decision-making |

The scenario-truth ledger never contains an executable action, an approval decision, or a priority label. Policy fixtures are separately owned by Honey/Ozzy. Joining artefacts for end-to-end evaluation requires their IDs, versions, and hashes to be recorded; it does not transfer authority between their owners.

### 4. Two-axis break and cause contract

ADR-002 owns the deterministic break symptom axis: `break_family`, `break_type`, affected field, source evidence, severity, and materiality. This ADR requires the generator to create a separate, small synthetic **cause** axis before it derives source mutations and observed symptoms.

The post-MVP candidate primary-cause vocabulary is:

- `SYNTHETIC_OPERATOR_ENTRY`;
- `SYNTHETIC_MAPPING_TRANSFORMATION`;
- `STALE_SOURCE_VERSION`;
- `DUPLICATE_OR_REPLAY`;
- `LATE_OR_REVISED_SOURCE`; and
- `UNKNOWN`.

Each mutated fixture must preserve a provenance graph:

```text
synthetic cause -> source mutation / delivery behaviour -> source observations
                -> deterministic difference fact(s) -> ADR-002 break type(s)
```

`UNKNOWN` is valid when a deterministic break is present but the synthetic evidence does not support a unique causal claim. The cause ID, scenario family, template, seed, expected action, and injection timestamps are prohibited from all future feature columns and LLM/RAG inputs. Multi-cause/multi-label targets are post-MVP.

### 5. Independent reconciliation oracle

The scenario oracle and production reconciliation implementation must be independently authored and dependency-isolated.

- The truth ledger stores intended mutation and expected difference facts, not results calculated by reconciliation code.
- Golden expected results are reviewed fixture data. The oracle may validate structured expected facts, but must not import production rules, normalisers, comparison helpers, or their transitive packages.
- The oracle and reconciler each record code/configuration hashes in the release manifest.
- A CI dependency check must fail if the oracle imports production reconciliation comparison code.
- Fizz independently reviews the dependency check and selected golden fixtures before their evidence can support a release gate.

At minimum, independent tests must prove these invariants against locked input snapshots:

| Invariant | Expected result |
| --- | --- |
| Coherent lifecycle | No deterministic break. |
| One declared mutation | The declared difference fact and break type are produced; undeclared changes are surfaced. |
| Transport replay with the same immutable event ID | No duplicated canonical effect or duplicate case. |
| Distinct business duplicate | The configured duplicate-control outcome is produced and cited. |
| Out-of-order arrival | A new versioned state/result is created without mutating earlier evidence. |
| Re-run on identical locked inputs | Identical result content and no duplicate break/case creation. |

This establishes testable determinism for the application result. It does **not** claim end-to-end "exactly once" execution of external UI actions.

### 6. Point-in-time and lineage-containment controls

Every evaluation record uses a locked `as_of_time`, source watermark, canonical-state version, reconciliation-result version, rule-set version, and content-addressed feature snapshot reference once ADR-001 defines that entity.

For any later classifier evaluation:

1. only observations available at `as_of_time` may enter a feature snapshot;
2. later confirmations, correction versions, human approvals, action outcomes, booking read-backs, final case closure, and scenario truth are prohibited;
3. a lifecycle root and every replay, correction, confirmation revision, and booking-observation descendant belong to one lineage group for split purposes;
4. random row-level splitting is prohibited; and
5. resampling, if ever justified, occurs only inside a training partition.

The exact feature schema and scoring grain are deferred until Honey's ADR-001 and ADR-002 are approved. The intended post-MVP classification grain is one `trade_break` version at one locked reconciliation result and `as_of_time`; case priority remains deterministic in MVP.

### 7. Temporal, family, and allocation tests

This ADR reserves three distinct post-MVP evaluation modes. None may be reported as observed production drift.

| Mode | Allocation rule | What it tests |
| --- | --- | --- |
| Stationary temporal replay | Forward-only time folds with a declared latency/correction embargo | Point-in-time reconstruction and reproducibility. |
| Controlled regime-shift suite | Later synthetic windows change one declared factor, such as source latency, schema version, family prevalence, portfolio mix, or calendar configuration | Robustness to documented synthetic shift. |
| Scenario-family holdout | A complete family/variant and all its lineage descendants are absent from development data | Safe abstention/`UNKNOWN` behaviour on unsupported patterns. |

Any future temporal model split must use forward-only chronological partitions; [scikit-learn's `TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) is illustrative only and does not replace this application-level availability contract.

To reduce generator shortcuts, the corpus manifest must include allocation matrices by product, pair configuration, notional band, value-date band, source-latency condition, and scenario family. Future model studies must add nuisance-only and label-permutation negative controls, plus cross-pair, cross-portfolio, and prevalence-shift slices. A model whose apparent performance is explained by scenario allocation rather than observable evidence is inadmissible.

### 8. Reproducibility and evidence

Each generator run creates a versioned evidence manifest containing:

- generator and oracle versions, code/configuration hashes, seed, UTC time range and calendar configuration;
- source-schema versions, fixture hashes, record counts, product/family/cause allocation, and lineage-group manifest;
- source observation and canonical/reconciliation fixture hashes;
- scenario-truth ledger hash and access classification;
- policy-fixture version/hash where an e2e scenario is evaluated; and
- test outcomes, evaluator identity, and evidence-artifact hashes.

The generator must reject baseline lifecycles that violate ADR-001's approved FX arithmetic, quote-orientation, decimal, date, and source-normalisation rules before any break is injected. Reproducing a run from the same approved generator version, configuration, seed, and dependency versions must reproduce the same fixture content hashes. CI proves this by running the fixture build twice in an isolated environment and comparing the manifests. Until that test exists and passes, the artefacts are described only as versioned fixtures, not as reproducible evidence.

## Consequences

### Positive

- Reconciliation evidence is less likely to repeat a common generator/oracle defect.
- Source facts, evaluator truth, policy ownership, and LLM context have explicit non-overlapping boundaries.
- Later ML work has a viable point-in-time, family-holdout, and lineage-containment contract.
- The data scope is small enough for deterministic local tests and review.

### Costs and limitations

- Independent oracle and golden-case authoring require additional review work.
- The 144-lifecycle corpus proves fixture and control coverage only; it cannot establish model quality, calibration, economic benefit, or real-market robustness.
- The chosen synthetic currency pairs, calendar, and source subset have no production coverage claim.
- Regulatory extracts, broad product coverage, multi-tenant testing, and live integrations are out of the first vertical slice.

## Required tests before implementation can claim this ADR is implemented

1. schema validation for each source-fixture type and its pinned version;
2. coherent-lifecycle, mutation, replay, business-duplicate, out-of-order, and deterministic-rerun invariants;
3. oracle/reconciler import-isolation check;
4. truth-field leakage scan across feature fixtures, prompts, RAG corpus, runtime API payloads, and traces;
5. lineage-group split validator and forward-only split manifest validator; and
6. deterministic two-run fixture-manifest comparison.

## Owner decisions still required

1. **Approved:** 144 lifecycles, one synthetic tenant and at least two synthetic portfolios; synthetic FX Spot/Forward only.
2. Approve the initial synthetic currency-pair and calendar configuration before fixture generation.
3. **Approved baseline:** ADR-001's canonical source/normalisation contract and ADR-002's eight break families. Field-specific tolerances and arrival windows remain versioned fixture configuration until separately set.
4. **Approved:** regulatory-extract fixtures remain post-MVP.
5. Approve the causal-label vocabulary as reference-only post-MVP training data, with `UNKNOWN` as a valid outcome.
6. **Approved:** independent-oracle and Fizz-review requirements are release-evidence gates.

## References

- [FpML FX product architecture](https://www.fpml.org/spec/fpml-5-6-5-rec-1/html/confirmation/fpml-5-6-intro-8.html)
- [FINOS CDM event model](https://cdm.finos.org/docs/event-model/)
- [scikit-learn `TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- `PLANS/TRADEOPS_REVIEW_HONEY_ON_SCOUT.md`
- `PLANS/TRADEOPS_SENTINEL_HONEY_INITIATION_CONTRIBUTION.md`
- `RESEARCH/TRADEOPS_SENTINEL_SOURCE_NOTES.md`
