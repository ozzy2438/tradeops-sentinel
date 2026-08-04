# RB-003: Automation failure and recovery procedure

Synthetic runbook for the reference implementation's controlled-AI
remediation slice. Scope: what to do when an automated action's outcome is
uncertain — most commonly, a timeout after the underlying write may already
have been applied.

## Section 1 — Never blindly resubmit

If an automated action times out, encounters a network error, or otherwise
returns an uncertain outcome, the system must never simply resubmit the same
instruction. A resubmission without first checking outcome risks applying the
same change twice, which is indistinguishable from an uncontrolled duplicate
action.

## Section 2 — Read back before deciding

The correct recovery step is to re-read the current state of the target
record and compare it against the intended, approved outcome. If the current
value already matches the approved value, and the record shows the same
idempotency key used for the original attempt, treat the action as already
successfully applied. Take no further write action.

## Section 3 — Idempotency key is the source of truth for "already applied"

Every signed action envelope carries a unique idempotency key. A record of
that key having already been applied to the target system is authoritative
proof the action already happened, independent of whether the original
caller received a success response. A second execution attempt presenting
the same idempotency key must be recognised as a replay, not a new action,
and must produce no second side effect.

## Section 4 — When a genuine retry is permitted

A new attempt against the same signed envelope is only permitted when the
read-back shows the target record does **not** yet reflect the approved
value and no prior application of that idempotency key is recorded. Even
then, the retry is still subject to every other envelope check: it must not
be expired, must not be tampered with, and must still match the expected
prior value it was originally issued against.

## Section 5 — Evidence of the recovery decision

Whichever path is taken — treating the action as already-applied via
read-back, or genuinely retrying — the evidence record for the case must
show which path was taken and why, so the recovery decision itself is
auditable, not just the eventual outcome.
