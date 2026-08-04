# RB-001: FX economic-value mismatch procedure

Synthetic runbook for the reference implementation's controlled-AI remediation
slice. Scope: `ECONOMIC_VALUE_MISMATCH` breaks on FX Spot/Forward trades where
the mismatching field is a payload economic amount (`base_amount`,
`terms_amount`, `quoted_rate`).

## Section 1 — Identify the authoritative source

The canonical field-provenance record on the trade's `ECONOMIC_VALUE_MISMATCH`
break always names the authoritative source for the mismatching field per the
approved `SourceOfTruthPolicy` (ADR-001). For every economic field in this
product's policy, `FIX_EXECUTION` has the highest precedence. Do not treat any
other source as authoritative for an economic field, regardless of how
recently it was received.

## Section 2 — Confirm the deviating source

The break's comparison evidence names the specific non-authoritative source
observation that diverges from the authoritative value beyond the configured
decimal tolerance. In the scope of this runbook, that deviating source is the
downstream legacy booking system (`MOCK_LEGACY_BOOKING`). Confirm the
deviating observation's `source_business_key` and `source_version` match the
trade under review before proposing any correction.

## Section 3 — Correction rule

When the deviating source is the legacy booking system and the authoritative
source is `FIX_EXECUTION`, the correct remediation is to update **only** the
deviating field on the legacy booking record to match the authoritative
value. The authoritative source is never altered. No other field on the
booking record may be changed as part of this procedure — a recommendation
that proposes correcting more than the single mismatching field, or proposes
correcting the authoritative source instead, is out of policy and must be
rejected.

## Section 4 — Approval requirement

Any correction to an economic field requires Maker and Checker approval by
two different identities before it may be executed. See RB-002. An
AI-generated recommendation is advisory input only; it is never itself an
approval and can never satisfy the Maker or Checker requirement.

## Section 5 — When not to recommend a correction

If the comparison evidence does not clearly attribute the deviation to a
single non-authoritative source, if the deviating field is not one of
`base_amount`, `terms_amount`, or `quoted_rate`, or if confidence in the
attribution is low, do not recommend an automated correction. Route the case
for manual investigation instead (`abstain_reason` populated, no
`recommended_action`).
