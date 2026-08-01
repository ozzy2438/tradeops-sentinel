# TS-3 examples

The examples are deliberately small, deterministic fixtures rather than
production data. `manifest.json` maps each document to its JSON Schema and
Pydantic model. Files under `valid/` must pass both validation layers. Files
under `invalid/` must be rejected by the stated semantic or schema rule; the
manifest records which layer must reject each fixture so contract-layer drift
cannot be hidden by an either/or test.

The valid observations and canonical projections cover both FX Spot and FX
Forward and keep execution, trade capture, confirmation, and booking as
separate observation kinds. The TS-4 fixtures cover the exact trade-break
taxonomy, an executable Spot/Forward matrix for all eight families, family
field/value types, comparison evidence bindings, typed missing-source arrival
windows, lifecycle resolution evidence, and deterministic priority inputs,
including an immutable reopened break version linked to its prior record. The
negative fixtures cover unsupported versions, temporal availability, decimal
scale, deterministic linkage decision/reason combinations, cross-scope
candidates and sources, provenance scope, duplicate source identity
semantics, invalid break transitions, conflicting break severity, same-ID
reopening, unknown or duplicate resolution evidence, and resolution-role
drift. Targeted contract tests also assert field-provenance key binding,
fail-closed settlement-rule and break-rule version handling, family comparison
field/value-type binding, resolution chronology, and missing-source
source-kind restrictions.
