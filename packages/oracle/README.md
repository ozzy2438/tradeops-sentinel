# TS-12 independent reconciliation oracle

This package is the independent expected-outcome oracle for the TS-11
reconciliation surface. Its public parity boundary is `OracleContext`, which
uses shared contract observations and canonical state while keeping the
family-level result separate from `ReconciliationRun`. The evaluator also has
a JSON-projection adapter for isolation-focused tooling and evidence.

The oracle is deliberately not a second production evaluator. It does not
import `packages.reconciliation`, read a `ReconciliationRun`, or call
production comparison helpers. It may use shared contract models, but its
fixture-only policy and family-level findings are independently represented in
`models.py` and `evaluator.py`.

The import-isolation gate scans all Python modules under `packages/` without
importing application code and fails closed on parse errors, unresolved
internal imports, dynamic imports, or any direct/transitive path between
`packages.oracle` and `packages.reconciliation`. The committed report is
`evidence/import-isolation.json`; the parity traceability matrix is
`evidence/parity-matrix.json`.

## Scope

- FX Spot and FX Forward parity across the eight ADR-002 families.
- Clean/no-break outcomes and typed break-family outcomes.
- Exact source-version and watermark validation at the oracle boundary.
- Machine-readable import graph and direct/transitive/parse-error negative
  evidence.

## Exclusions

TS-13 rerun determinism and duplicate-conflict milestone work, TS-11 rule or
tolerance changes, generator/corpus expansion, E6 TS-14/TS-15, LLM/workflow,
ML, UI, cloud or paid services, RPA/action execution, deployment, production
writes, and unrelated refactors.

Owner: Honey + Bumble. Independent assurance: Fizz. Traceability: issue #12,
ADR-014, MVP Release Charter §31.
