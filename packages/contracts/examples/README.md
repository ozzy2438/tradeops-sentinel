# TS-3 examples

The examples are deliberately small, deterministic fixtures rather than
production data. `manifest.json` maps each document to its JSON Schema and
Pydantic model. Files under `valid/` must pass both validation layers. Files
under `invalid/` must be rejected by the stated semantic or schema rule.

The valid observations cover both FX Spot and FX Forward and keep execution,
trade capture, confirmation, and booking as separate observation kinds. The
negative fixtures cover an unsupported contract version, event-time/ingest
ordering, decimal-scale mismatch, and cross-portfolio linkage.
