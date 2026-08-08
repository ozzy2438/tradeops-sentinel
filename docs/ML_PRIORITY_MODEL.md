# Explainable LightGBM priority model

This post-MVP extension adds one local, versioned LightGBM binary classifier
that estimates the probability that a detected trade-break case needs
escalated handling. A fixed threshold policy converts that probability into
`LOW`, `MEDIUM`, `HIGH`, or `CRITICAL` queue priority.

It is deliberately advisory. Deterministic reconciliation still decides what
is broken; the deterministic policy engine still decides whether a proposed
correction is eligible; Maker and Checker still approve; and the signed
action envelope still controls the only permitted write. The ML score is not
an input to any of those authorisation checks.

## Reproducible model tuple

The immutable tuple is:

- model: `packages/priority_model/artifacts/priority_model.txt`;
- metadata and SHA-256 binding: `packages/priority_model/artifacts/metadata.json`;
- model version: `priority-lgbm-1.0.0`;
- feature version: `1.0.0`;
- runtime/training library: `lightgbm==4.7.0`;
- training seed: `20260808`;
- data: 2,400 generated point-in-time cases, split 1,920/480 before training.

Run the deterministic validation gate with:

```bash
python -m pip install -e ".[dev,ml]"
python scripts/train_priority_model.py --check
```

The check regenerates the synthetic dataset, verifies its hash, verifies the
model hash and pinned LightGBM version, then reruns the stored holdout metrics.
Training is an explicit separate command:

```bash
python scripts/train_priority_model.py
```

## Features and leakage boundary

`packages/priority_model/features.py` is the complete feature contract. It
uses only the strict `BreakFacts` already available when a case is created:
trade-value magnitude, relative value gap, product type, break-family flags,
bounded field/source indicators, condition, and G10-currency membership.

It never receives evaluator scenario truth, a resolution outcome, approval,
post-action read-back, or any future information. Each synthetic trade has one
independent row, so a trade lineage cannot cross the train/validation split.

## SHAP evidence

Inference calls LightGBM with `pred_contrib=True`. LightGBM documents this
output as per-feature SHAP contributions plus a final expected/base value:
<https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.Booster.html>.
The implementation records every feature value and contribution, sorts them
by absolute effect for display, and rejects the result unless:

```text
SHAP base value + sum(feature contributions) == raw LightGBM score
```

within `1e-6`. The API, append-only assessment row, frozen evidence document,
and dashboard all expose the version, score, priority band, and SHAP evidence.

## Honest interpretation

The checked-in metadata contains the exact holdout metrics. They demonstrate
that the model-training, versioning, inference, explanation, persistence, and
validation pipeline works. They do **not** establish predictive validity on
real operations: all inputs and escalation labels are synthetic. A production
deployment would require approved historical labels, temporal/out-of-time
validation, bias and drift monitoring, calibration, and formal model-risk
governance.
