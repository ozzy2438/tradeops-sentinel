# TS-11 deterministic reconciliation

This package implements the E5 reconciliation slice from ADR-002. It is a pure,
read-only evaluator: it consumes an exact, versioned source set and a canonical
state, then emits an append-only `ReconciliationRun` containing typed
`TradeBreak` records and flattened `BreakFact` evidence.

## Scope

The evaluator implements these eight families, in the contract-defined order:

| Family | Deterministic predicate | Configuration input |
| --- | --- | --- |
| `MISSING_REQUIRED_SOURCE` | expected source is absent after its arrival window | product/source arrival window |
| `AMBIGUOUS_OR_UNMATCHED_LINKAGE` | accepted linkage is not exactly one in-scope candidate | typed linkage decision |
| `DUPLICATE_SOURCE_CONFLICT` | same kind/key/version has non-identical content | none |
| `CURRENCY_PAIR_OR_SIDE_MISMATCH` | normalised currency or side differs | normalisation rule version |
| `ECONOMIC_VALUE_MISMATCH` | Decimal difference exceeds configured tolerance | product/field Decimal tolerance |
| `TRADE_OR_VALUE_DATE_MISMATCH` | normalised dates differ | normalisation rule version |
| `LIFECYCLE_STATUS_MISMATCH` | source lifecycle relation is not the approved relation | lifecycle rule version |
| `POST_ACTION_VERIFICATION_FAILURE` | readback is unavailable, changed, or the original break remains | read-only verification evidence |

No LLM, ML score, arbitrary SQL, database write, booking write, or production
rule activation is present in this package. TS-12 oracle/import isolation,
TS-13 replay-invariant work as a separate milestone, generator expansion,
cloud, UI, and action execution remain out of scope.

## API

```python
from packages.reconciliation import ReconciliationEngine, fixture_config

run = ReconciliationEngine(fixture_config()).run(context)
```

`ReconciliationContext` rejects cross-scope observations, observations after
the canonical watermark, duplicate observation IDs, and any source set that
does not exactly equal `CanonicalTradeState.source_version_set`. Every output
break records the configuration hash, source references, field comparisons,
evidence roles, lifecycle transition, and deterministic versions.

`fixture_config()` is explicitly marked `FIXTURE_ONLY`; its synthetic values
are reproducibility inputs for local tests, not operational thresholds.

Owner: Honey + Bumble. Independent assurance/oracle-boundary review: Fizz.
Epic E5 / issue #11. No cloud or production action is authorized by this
package.
