# E3 deterministic synthetic FX lifecycle generator

This package generates the approved synthetic FX Spot and FX Forward lifecycle
population for E3. It is a fixture generator, not a market-data simulator and
does not make claims about live FX coverage.

`generate_corpus()` produces, deterministically for a `GeneratorConfig` seed:

- 48 clean lifecycle scenarios;
- 96 controlled mutations: six per TS-4 break family and product;
- execution, trade-capture, booking, and confirmation source observations;
- UTC event, effective, and ingestion timestamps;
- opaque deterministic runtime identifiers with population order shuffled before
  identifier and timestamp allocation;
- evaluator-only scenario truth and a product/family coverage manifest.

The source observations are validated with the merged TS-3 Pydantic contracts.
Truth metadata (cause, mutation, seed, expected difference facts, and provenance)
is kept outside `runtime_bundle()` and must not be passed to runtime
reconciliation, LLM context, features, retrieval, UI, or traces. The
evaluator-only provenance graph explicitly links cause → source mutation and
delivery behaviour → observations → difference facts → break family. Tests scan
runtime values (not only keys) for mutation/template markers and reject
non-UTC-offset configurations.

To materialise deterministic JSON artefacts for an evaluator:

```python
from pathlib import Path

from packages.generator import generate_corpus

generate_corpus().write_to(Path(".scratch/e3-fixtures"))
```
