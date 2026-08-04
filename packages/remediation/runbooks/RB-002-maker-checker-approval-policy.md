# RB-002: Maker-checker approval policy

Synthetic runbook for the reference implementation's controlled-AI
remediation slice. Scope: approval requirements for any automated
remediation action proposed against a reconciliation break.

## Section 1 — When two approvals are required

Any recommended action that changes an economic field (`base_amount`,
`terms_amount`, `quoted_rate`) on any system of record — including the
synthetic legacy booking system used in this reference implementation —
requires exactly two approvals before execution: one Maker and one Checker.

## Section 2 — Maker and Checker must be different identities

The Maker and the Checker must be two distinct, identifiable individuals. A
single identity submitting both approvals is not a valid two-person control
and must be rejected at submission time, not discovered later.

## Section 3 — The AI cannot approve its own recommendation

An AI-generated recommendation is advisory input to a human decision. The
system that produced the recommendation is never a valid Maker or a valid
Checker for that same recommendation, regardless of the confidence score
attached to it.

## Section 4 — Confidence and manual investigation

A recommendation whose `confidence` score is below the configured threshold
is not eligible for the Maker/Checker approval flow at all. It must be routed
to manual investigation instead, with `abstain_reason` explaining why
automated remediation was not proposed.

## Section 5 — Missing or invalid citation blocks execution

A recommendation without a citation to an approved runbook section, or whose
citation does not support the recommended action, cannot proceed to approval
or execution under any circumstance. This is a fail-closed control: absence
of a citation is treated identically to an invalid one.

## Section 6 — Prohibited actions

The following actions are never within scope for this Maker/Checker flow and
must be rejected outright regardless of AI confidence or approval: order
submission, order cancellation, order amendment, any price or rate decision,
and any live trading action. This policy governs post-trade data correction
only.

## Section 7 — Scope of an approved change

An approval authorizes exactly the single field and single approved value
named in the recommendation that was reviewed. It does not authorize any
other field, any other trade, or a different value arrived at after the
approval was given.
