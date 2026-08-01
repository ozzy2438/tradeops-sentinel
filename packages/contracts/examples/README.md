# TS-3 examples

The examples are deliberately small, deterministic fixtures rather than
production data. `manifest.json` maps each document to its JSON Schema and
Pydantic model. Files under `valid/` must pass both validation layers. Files
under `invalid/` must be rejected by the stated semantic or schema rule; the
manifest records which layer must reject each fixture so contract-layer drift
cannot be hidden by an either/or test.

The valid observations and canonical projections cover both FX Spot and FX
Forward and keep execution, trade capture, confirmation, and booking as
separate observation kinds. The negative fixtures cover unsupported versions,
temporal availability, decimal scale, deterministic linkage decision/reason
combinations, cross-scope candidates, provenance scope, and duplicate source
identity semantics. Targeted contract tests also assert field-provenance key
binding and fail-closed settlement-rule version handling.
