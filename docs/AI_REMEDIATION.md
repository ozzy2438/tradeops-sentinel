# Controlled-AI remediation slice

One small, complete, visible controlled-AI remediation workflow layered on
top of the existing deterministic FX reconciliation MVP. It demonstrates
end-to-end, for exactly one break family and one field, how an AI-generated
recommendation can lead to a real, auditable correction without the AI ever
touching a system directly.

**This is not an autonomous remediation platform.** It supports exactly one
scenario. Extending it to other break families, fields, or products is
explicitly out of scope for this slice — see [Scope](#scope) below.

## The one supported scenario

`ECONOMIC_VALUE_MISMATCH` on `/payload/base_amount`, where the legacy
booking system (`MOCK_LEGACY_BOOKING`) disagrees with the authoritative
execution value (`FIX_EXECUTION`). Nothing else — a different break family, a
different field, or a value the AI proposes that doesn't match the
reconciliation engine's own authoritative source is rejected, not
generalised to.

## Workflow

1. The existing deterministic reconciliation engine detects the break
   (unchanged — `packages/reconciliation`).
2. `packages/remediation/triage.py` builds a strict `BreakFacts` document
   from the break's own structured comparison data — no raw source documents,
   no database access from the AI's side.
3. An AI provider (`packages/remediation/ai_provider.py`) returns a strict
   `AIRecommendation`: predicted root cause, confidence, priority,
   recommended action, proposed field/value, risk tier, citations, or an
   abstain reason. The LLM never executes SQL, calls a tool, or modifies
   anything — it returns one JSON document, validated against the schema
   before anything downstream sees it.
4. The recommendation must cite a real runbook section
   (`packages/remediation/retrieval.py`, `packages/remediation/runbooks/`).
5. `packages/remediation/policy.py`, a deterministic engine, evaluates the
   recommendation against a fixed rule set. It never trusts the AI's own
   `required_approvals` or `risk_tier` — those are advisory; the policy
   engine's own decision is authoritative.
6. If eligible, a Maker and a different-identity Checker approve
   (`POST /remediation/cases/{id}/maker-approval` /
   `.../checker-approval`).
7. A signed, expiring, idempotent `ActionEnvelope` is built and issued once
   per case (`packages/remediation/envelope.py`).
8. `RemediationExecutor` (`packages/remediation/executor.py`) verifies the
   envelope and approvals, then calls `MockLegacyBookingAdapter` to apply
   the one approved field.
9. The corrected value is read back, then re-delivered into the normal
   observation-ingestion pipeline as a new, superseding `BOOKING`
   observation (`apps/api/service.py::ingest_corrected_booking_observation`)
   — this is what makes the correction visible to reconciliation at all; see
   [Why a new observation, not a canonical-state patch](#why-a-new-observation-not-a-canonical-state-patch).
10. The existing deterministic reconciliation pipeline reruns, scoped to
    just this trade (`apps/api/service.py::rerun_trade_reconciliation`), and
    confirms the break is resolved.
11. A frozen, hashed evidence record is written, preserving the original
    break, the AI recommendation, citations, confidence, the policy
    decision, both approvals, the envelope's content hash, every execution
    attempt, and the post-action reconciliation result
    (`packages/remediation/evidence.py`).

## AI provider

One live provider is implemented: `AnthropicProvider`
(`packages/remediation/ai_provider.py`), using the `anthropic` Python SDK
against `claude-sonnet-5` by default. **No live Anthropic credential was
available while building this slice.** `AnthropicProvider`'s request/response
handling is implemented and structurally correct, but it has not been
exercised against a real API call, and nothing in this repository claims
otherwise.

`DeterministicTestProvider` is the default (`TRADEOPS_AI_PROVIDER` unset) and
is what every test and this demo actually run against. It is a plain
rule-based provider, not a model: it supports exactly the one scenario above
and abstains (with `abstain_reason: "unsupported_break_pattern"`) for
anything else. Set `TRADEOPS_AI_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`
to select the live provider; nothing else changes.

## Runbook retrieval

Three short synthetic documents (`packages/remediation/runbooks/`):

- `RB-001` — FX economic-value mismatch correction procedure
- `RB-002` — Maker-checker approval policy
- `RB-003` — Automation failure and recovery procedure

Retrieval is local keyword-overlap search over the parsed section index
(`packages/remediation/retrieval.py::search`) — no vector database, no RAG
platform. Search relevance alone is never trusted: the actual fail-closed
gate is `citation_supports_action`, a small hardcoded allowlist of which
runbook sections can back which action. A citation that resolves to a real
document/section but isn't on that allowlist is rejected identically to a
missing one.

## Policy and approval rules

Implemented in `packages/remediation/policy.py` and
`packages/remediation/executor.py`, in this order:

1. Confidence below `0.7` → `MANUAL_INVESTIGATION`, before anything else is
   even inspected.
2. The AI abstaining (`recommended_action: null`) → `MANUAL_INVESTIGATION`.
3. Any action outside the closed allow-list
   (`{"CORRECT_LEGACY_BOOKING_FIELD"}`) → `REJECTED`. Order
   submit/cancel/amend, price decisions, and trading actions cannot even be
   expressed by the `AIRecommendation` schema's `RecommendedAction` Literal.
4. Missing citation → `REJECTED`.
5. Citation present but not on the action's support allowlist → `REJECTED`.
6. Proposed field outside `ALLOWED_PROPOSED_FIELDS`
   (`{"/payload/base_amount"}`), not matching the break's own field, or a
   value that doesn't exactly equal the reconciliation engine's own
   authoritative value → `REJECTED`. The AI's restated number is never
   trusted; it must reproduce what the engine already established.
7. Economic-field correction, eligible → `required_approvals: ["MAKER",
   "CHECKER"]`, hardcoded — independent of whatever the AI itself claimed.
8. Maker and Checker must be different identities — enforced at approval
   submission (API layer) and again at execution (`RemediationExecutor`,
   bound to the identities recorded on the signed envelope).
9. The AI cannot approve its own recommendation — there is no
   AI-driven approval path; `MAKER`/`CHECKER` decisions only ever come from
   the two approval endpoints, each requiring a human-supplied identity.
10. Only the exact approved field and value may change — enforced by the
    envelope's `field_path`/`approved_value`, checked against the allow-list
    again at execution time, independent of the policy decision that built it.

## Signed action envelope

`ActionEnvelope` (`packages/remediation/models.py`) carries: `case_id`,
`trade_id`, `action_type`, the exact approved field path and value, the
expected old value, maker/checker identity, `issued_at`/`expires_at`,
`idempotency_key`, `content_hash`, and `signature`.

Signing uses HMAC-SHA256 over a canonical JSON serialisation of every field
except the hash and signature themselves
(`packages/remediation/envelope.py::build_envelope` /
`verify_envelope`), keyed by a **local secret read from the
`TRADEOPS_REMEDIATION_SIGNING_SECRET` environment variable. This secret is
never committed anywhere in this repository.**

The executor rejects, in order: an expired envelope, a tampered envelope
(content or signature mismatch — checked before expiry, so a
modified-and-expired envelope is reported as tampered), missing or
incomplete approvals, same-identity Maker/Checker, a field outside the
approved allow-list, and — delegated to the mock adapter's row-locked
check — an unexpected current value or a replay that would create a second
side effect.

## Mock legacy execution

**UiPath-ready controlled action contract demonstrated through a mock
legacy-booking adapter.** `MockLegacyBookingAdapter`
(`packages/remediation/legacy_adapter.py`) is the exact boundary a UiPath
robot, or any other legacy-system integration, would sit behind: read the
current record, apply exactly one verified field change under a signed
envelope, report what happened. **No real UiPath environment is installed,
configured, or connected anywhere in this repository.** This is a mock of
that boundary, not an integration with it.

This mock is a plain Python/PostgreSQL adapter, deliberately simpler than
the browser-automation executor
[`docs/adr/ADR-011_LEGACY_AUTOMATION_RUNTIME_AND_COST_DECISION.md`](adr/ADR-011_LEGACY_AUTOMATION_RUNTIME_AND_COST_DECISION.md)
proposes for a fuller MVP (a Playwright-driven mock legacy *application*,
still unimplemented, status: draft). This slice's task explicitly excluded
building that: no web app to automate, no browser, no UiPath quote or
environment — just the signed-envelope → verify-then-write → read-back
contract a UiPath robot or a Playwright executor would eventually sit
behind.

## Post-action verification and idempotency

`RemediationStore.apply_legacy_booking_correction` row-locks
(`SELECT ... FOR UPDATE`) the target record for the duration of a
check-then-write: if the envelope's `idempotency_key` was already applied,
it returns `DUPLICATE_NOOP` with the current value and makes no write; if
the current value doesn't match the envelope's `expected_old_value`, it
returns `VALUE_MISMATCH` and makes no write; otherwise it applies the
correction and records the idempotency key.

The row lock, not application-level caching, is what actually prevents a
second side effect on replay. `RemediationExecutor.execute` accepts an
`attempt_context` of `"TIMEOUT_RECOVERY_ATTEMPT"`, which reports the
identical no-write path as `TIMEOUT_RECOVERED` instead of `DUPLICATE_NOOP` —
same underlying safety mechanism, a distinct, honestly-labelled outcome for
the "did that actually go through after a timeout?" recovery narrative
versus a plain replay.

### Why a new observation, not a canonical-state patch

The reconciliation engine compares its policy-authoritative baseline against
every other observation in a trade's full source set and flags a break on
the first divergent one it finds — it does not resolve "latest revision per
source system" the way the canonical assembler does. Directly overwriting
canonical state after a correction would make the dashboard look right while
lying about what the deterministic engine actually re-derives from source
data, and the original bad `BOOKING` observation would still be sitting in
the set, ready to reproduce the break on the next full run.

Instead, `ingest_corrected_booking_observation` re-delivers the fix as a new
`BOOKING` observation through the exact same ingestion path every other
source system uses, with `supersedes_observation_id` set to the revision it
corrects. `_reconcile_lineage_group`
(`apps/api/service.py`, shared by both the full-corpus `run_reconciliation`
and the scoped `rerun_trade_reconciliation`) excludes whatever a correction
has explicitly superseded before handing observations to the unmodified
engine. `supersedes_observation_id` carries no other filtering behaviour
anywhere else in this codebase — every other lineage group in the demo
corpus never sets it, so this is a verified no-op for the other eleven
`ECONOMIC_VALUE_MISMATCH` scenarios and every other break family. The
correction is durable: a later `POST /reconciliation/run` full batch pass
still shows the corrected trade as clean, not just the one scoped rerun
immediately after execution.

## API

Five endpoints, added to the existing FastAPI service, behind the same
`X-API-Key` guard as every other endpoint:

- `POST /remediation/cases` — generate an AI recommendation and policy
  decision for one break.
- `POST /remediation/cases/{case_id}/maker-approval`
- `POST /remediation/cases/{case_id}/checker-approval`
- `POST /remediation/cases/{case_id}/execute` — build/reuse the signed
  envelope, execute, and on a genuine `applied` outcome, ingest the
  corrected observation, rerun scoped reconciliation, and finalise evidence.
- `GET /remediation/cases/{case_id}/evidence` — full case state at any
  stage; the frozen evidence snapshot once execution has succeeded.

No new user-management system, RBAC, or authentication mechanism — approver
identity is a free-text field on the approval request, matching this
slice's "reuse the existing API, do not build RBAC" scope.

## Dashboard

The existing break-detail section of `apps/dashboard/app.py` gained one new
subsection, "AI-assisted remediation" — nothing else was redesigned. It
shows the detected break, predicted root cause, confidence/priority, cited
runbook sections, proposed correction, risk tier, Maker/Checker approval
controls, execution status and attempt history, post-action verification
(`RECONCILED` once the scoped rerun confirms `PASS`), and the evidence record
identifier and content hash.

## Tests

`tests/test_remediation.py` (23 tests, no database) — structured AI output
schema, deterministic policy fail-closed rules, envelope tamper/expiry
detection.

`tests/integration/test_remediation_e2e.py` (18 tests, requires
`TRADEOPS_TEST_DATABASE_URL`) — break detection, Maker≠Checker,
insufficient-approval rejection, unapproved-field rejection,
expected-old-value-mismatch rejection, expired-envelope rejection, first
successful execution, replay safety, timeout-recovery read-back, post-action
reconciliation (including the full-batch durability property above), and
complete evidence-record content.

## Scope

**In scope for this slice:** exactly what is described above, for exactly
one break family and one field.

**Explicitly out of scope**, per the task that produced this slice: root-cause
model training, priority-model training, SHAP, MLflow, a real UiPath
installation, cloud deployment infrastructure, OAuth, a general RBAC system,
new asset classes, new reconciliation rules or break families beyond the
existing eight, autonomous trading, production booking-system integration,
additional dashboards, and any further product-roadmap expansion. This PR
does not begin a next phase and does not propose one.
