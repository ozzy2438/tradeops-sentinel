# TradeOps Sentinel — MVP Release Charter (Consolidated, Final)

**Global Markets Trade-Break Automation & Regulatory Evidence Platform**

Status: **APPROVED by owner 2026-07-31 (event `7e646911`) — implementation baseline. Sprint 1 (foundations only, E1–E6) AUTHORISED.** Owner-resolved bindings: private monorepo `tradeops-sentinel`; Playwright MVP executor; **corpus = 144 scenarios**; evidence retention = 180 days; Honey canonical contracts = baseline. **Still excluded/unauthorised in Sprint 1:** cloud provisioning, paid services, UiPath execution, the LLM workflow implementation, model training, and production deployment.

This charter is not a summary — it is the single internally consistent contract. Where two ADRs used different wording, this charter **normalises to one canonical form** (see "Consistency Pass" at the end) and the affected ADR wording conforms to it during Sprint 1 Epic 2. Source ADRs: `PLANS/adr/ADR-001…014`. Team consistency passes folded in: `PLANS/TRADEOPS_SENTINEL_{FIZZ,HONEY,SCOUT}_*CONSISTENCY*.md`.

---

## 1. Executive Recommendation
**READY WITH OWNER DECISIONS.** The 14 ADRs close every Critical/High architecture-review finding and, after the normalisations in this charter, contain **no unresolved cross-ADR contradiction**. The remaining items are **owner decisions**, not conflicts. Sprint 1 (foundations only) can begin on approval.

## 2. Final MVP Product Statement
A local-first, synthetic-only reference platform that ingests FIX-style execution + FpML-style confirmation observations, builds a versioned canonical FX trade state, **deterministically** detects a bounded set of trade breaks, opens and prioritises a deterministic exception case, uses **one constrained advisory LLM** to produce a cited investigation/remediation recommendation (or abstain), routes any material change through **maker-checker**, compiles + signs an allow-listed, expiring, idempotent action instruction, executes **one** controlled non-economic update against a mock legacy booking app via a free deterministic Playwright executor behind an Action Gateway, verifies by **read-back + re-reconciliation**, and emits a **tamper-evident** evidence package. AI is advisory and read-only; humans control every material change; no claim is made that is not enforced and tested.

## 3. Final Vertical-Slice Scope (13 steps)
1. Generate synthetic FX Spot + Forward lifecycle events → 2. Ingest + validate FIX-style execution + FpML-style confirmation → 3. Build canonical trade state → 4. Detect the bounded deterministic break set (ADR-002, 8 families) → 5. Create + deterministically prioritise one exception case (one trade/one portfolio) → 6. One constrained LLM (`investigate_and_recommend`) → cited recommendation or abstain → 7. Maker-checker on material actions → 8. Deterministic compiler + signer produce a signed, allow-listed, expiring, idempotent instruction → 9. Execute one `SET_CONFIRMATION_REFERENCE` (non-economic) update via Playwright executor behind the Action Gateway → 10. Read-back-before-write + post-action read-back verification → 11. Reconcile final state → 12. Tamper-evident evidence package (per-stream SHA-256 chain + verifier) → 13. CI demonstrates a success path AND an uncertain-execution recovery path. **Material economics remain manual-only.**

## 4. Explicit Post-MVP Scope
Second LLM verifier · learned root-cause classifier · learned priority model · calibration/SHAP/MLflow · anomaly detection · multi-label classification · similar-case retrieval · standalone RAG/parser microservices · managed streaming (SQS/Kinesis/Kafka) · Kubernetes · multi-region / DR-HA · WORM/legal-hold/non-repudiation · **real UiPath** (attended/unattended/serverless — gated on a vendor quote) · regulatory-reporting-extract source · cross-trade/netting cases · multi-tenant administration · live market connectivity · autonomous trading. Full ML roadmap: `PLANS/TRADEOPS_SENTINEL_POST_MVP_ML_ROADMAP.md`.

## 5. Approved Architecture Diagram
```
synthetic FX generator (fixtures + scenario-truth ledger, evaluator-only, dependency-isolated oracle)
      │
┌─ one application container ─────────────────────────────────────────────┐
│  ingestion+FIX/FpML parser · deterministic reconciliation · constrained  │
│  LLM workflow (read-only tools) · RAG/citation adapter · Action Gateway   │
│  (own DB role) · evidence hash-chain + verifier                          │
└──────────────────────────────────────────────────────────────────────────┘
      │                                   │
  PostgreSQL 16 + pgvector            mock legacy booking web app (own container, network hop)
  (canonical state · source_event_inbox/outbox ·                    ▲
   SELECT…FOR UPDATE SKIP LOCKED work queue ·                       │ read-back-before-write
   action_attempts ledger · audit_events insert-only ·      Playwright executor (free, headless)
   RAG index · evidence)                                            │ (no AWS/DB credential)
      │                                                             │
  deterministic reconciliation → break? → deterministic case+priority → LLM (or ABSTAIN)
      → deterministic policy → maker → checker → deterministic compiler → signer
      → Action Gateway (2× dispatch-eligibility) → executor → read-back + re-reconciliation
      → VERIFIED_APPLIED | VERIFIED_NOT_APPLIED | ESCALATED → tamper-evident evidence + verifier gate
```
No managed broker, no second LLM, no standalone microservices, **no learned-ML module** in the MVP runtime.

## 6. Runtime & Component Inventory (MVP)
| Component | Purpose | Notes |
|---|---|---|
| PostgreSQL 16 + pgvector | canonical state, inbox/outbox, work queue (`FOR UPDATE SKIP LOCKED`), `action_attempts`, `audit_events` (insert-only role), RAG index, evidence | only stateful service |
| One application container | ingestion/parser, deterministic reconciliation, constrained LLM workflow, RAG/citation adapter, **Action Gateway** (own least-privilege DB role) | modules, not services |
| Mock legacy booking web app | UI-only write surface exercised over a network hop; must expose a CAS/lease seam at final submit | own container |
| Playwright executor | deterministic legacy automation behind the Action Gateway; **no AWS/DB credential** | free, headless |
**Explicitly NOT in the MVP runtime (per consistency item C-05):** any learned ML inference module or model/calibrator/feature tuple. A typed inference interface is reserved as a post-MVP seam with **no model artefact and no CI gate**.

## 7. Data-Flow & Control-Flow Diagrams
**Data flow:** source fixtures → `source_event_inbox` (unique on source **identity/version**; `content_hash` stored separately) → normalised observations → `canonical_trade_state_versions` → `reconciliation_runs` → `trade_breaks` → `exception_cases` → `case_evidence_snapshots` (frozen) → LLM recommendation → `policy_decisions` → approvals → `action_instruction_draft` → `signed_action_instructions` → executor → `action_readbacks` → `post_action_verifications` → `final_outcomes`; every step emits `audit_events` (hash-chained).
**Control flow:** deterministic services own truth + routing; the LLM is a single advisory node; humans (maker, distinct checker) authorise material change; the Action Gateway is the only component that can attempt a write, gated by two deterministic dispatch-eligibility checks; closure requires read-back + re-reconciliation + audit-completeness.

## 8. Canonical Trade & Observation Model (ADR-001)
One synthetic tenant, **≥2 portfolios** (C-06); one canonical trade = one tenant+portfolio; one case = one trade = one portfolio (may aggregate multiple breaks for that trade). Entities: `source_observations, execution_observations, confirmation_observations, booking_observations, regulatory_extract_observations` (post-MVP), `trade_linkage_candidates, linkage_decisions, canonical_trades, canonical_trade_state_versions, reconciliation_runs, trade_breaks, exception_cases, evidence_items, case_evidence_snapshots`, and `feature_snapshots` (**post-MVP seam only, no MVP dependency**). Every material entity carries a deterministic non-reused id, schema version, tenant/portfolio, correlation/causal ref, timestamps, actor identity, and version/supersession metadata; new versions supersede — no destructive update. FX economics use **decimal** arithmetic (binary float prohibited), quote orientation `terms_currency_per_base_currency`, side relative to base; original source values retained as evidence.

## 9. Source-of-Truth Precedence Matrix (ADR-001 — sole authority)
| Field / fact | Trusted source | Canonical rule |
|---|---|---|
| Execution existence/ID/product/economics | FIX-style execution/capture | Authoritative; disagreement → break, never silent overwrite |
| Confirmation existence/status/terms | FpML-style confirmation | Authoritative for its own status/content; disagreement w/ execution → break |
| Current legacy-booking values/version | Fresh booking read-back | Authoritative only for what the mock app currently stores |
| Portfolio/book assignment | Execution/capture unless owner-approved correction decision | Versioned precedence; human correction may supersede projection, never rewrites source |
| Regulatory extract content (post-MVP) | Regulatory extract | Authoritative for what was reported, not for what the trade ought to be |
| Observation linkage | Versioned deterministic rule or governed human decision | Ambiguous linkage cannot populate authoritative fields |
| Break fact/status | Deterministic reconciliation service | Only the rule version evaluating an exact source-version set creates/resolves a break |
| Action outcome | Fresh read-back + post-action reconciliation | Executor receipt alone is never proof |

## 10. Deterministic Trade-Break Taxonomy (ADR-002 — 8 families)
`MISSING_REQUIRED_SOURCE` (High/Med) · `AMBIGUOUS_OR_UNMATCHED_LINKAGE` (High) · `DUPLICATE_SOURCE_CONFLICT` (High) · `CURRENCY_PAIR_OR_SIDE_MISMATCH` (Critical) · `ECONOMIC_VALUE_MISMATCH` (Critical) · `TRADE_OR_VALUE_DATE_MISMATCH` (High) · `LIFECYCLE_STATUS_MISMATCH` (High) · `POST_ACTION_VERIFICATION_FAILURE` (Critical, never auto-resolved from a receipt). `BOOKING_VERSION_CONFLICT` is a safety state (ADR-005/013), not overwrite permission. Break lifecycle: `OPEN → UNDER_INVESTIGATION → RESOLUTION_PROPOSED → ACTION_PENDING | NO_ACTION_DISPOSITION_PENDING → VERIFYING → RESOLVED | ESCALATED`. Symptom axis is separate from a post-MVP causal axis; the LLM cannot alter any break fact/severity/priority. **`DUPLICATE_SOURCE_CONFLICT` normalisation (C-09):** the inbox is uniquely indexed on source **identity/version**; exact-hash replay deduplicates (idempotent), but the same identity/version with **different** content deterministically raises `DUPLICATE_SOURCE_CONFLICT`. Tolerances/arrival-windows/materiality are owner-approved configuration — no operationally-meaningful threshold is claimed until set.

## 11. Workflow State Machine (ADR-003 — canonical)
`OBSERVATION_RECEIVED → INPUT_VALIDATED → TRADE_STATE_ASSEMBLED → RECONCILED → NO_BREAK_CLOSED | BREAK_OPENED → PRIORITISED → INVESTIGATING → RECOMMENDATION_VALIDATING → RECOMMENDATION_READY | AGENT_ABSTAINED → READY_FOR_POLICY → NO_ACTION_REVIEW | MAKER_REVIEW_PENDING → MAKER_APPROVED → CHECKER_REVIEW_PENDING → CHECKER_APPROVED → ACTION_COMPILED → ACTION_SIGNED → DISPATCH_ELIGIBLE → ACTION_DISPATCHED → READBACK_PENDING | EXECUTION_UNCERTAIN → POST_ACTION_VERIFYING → VERIFIED_APPLIED → FINAL_RECONCILIATION → CLOSED`. Controlled/terminal: `INPUT_REJECTED, LINKAGE_REVIEW_PENDING, MORE_EVIDENCE_REQUIRED, MAKER_REJECTED, CHECKER_REJECTED, ACTION_CANCELLED, ACTION_REVOKED, ACTION_EXPIRED, ACTION_SUPERSEDED, VERIFIED_NOT_APPLIED, POST_ACTION_FAILED, ESCALATED, CLOSED_NO_ACTION`. Expected-version compare-and-set on every transition; any newer material observation supersedes stale review/action authority and returns to `RECONCILED`/`ACTION_SUPERSEDED`/`ESCALATED`. This is the **single canonical state enum** (consistency C-02); the executable action sub-lifecycle (§14) uses the `ACTION_*` names verbatim.

## 12. Human-Control & Maker-Checker Rules (ADR-003)
Human-review routing is **deterministic policy output** — the LLM cannot select an approver, create a review, or set role/materiality. All booking writes require a maker **and a distinct checker** (never the same identity/agent); material economics are manual-only unless the ADR-005 final-submit control is approved+proved. Overrides are bounded + audited (identity, role, reason, scope, expiry, exact evidence/policy versions) and **cannot** waive maker-checker, signature, expiry, revocation, version/lease, or read-back checks, nor make an LLM/executor an approver, nor close a failed reconciliation. Review expiry escalates — never auto-approves.

## 13. Agent & Tool-Authority Boundaries (ADR-004)
One LLM workflow `investigate_and_recommend`: reads one frozen `case_evidence_snapshot`, retrieves only approved versioned runbooks, explains the deterministic break, proposes a bounded resolution, cites every material claim — or abstains. Output = versioned `InvestigationRecommendation` (a **non-executable `proposed_resolution`**, no exact old/new action values). Read-only tools only: `get_canonical_trade_state, get_source_observations, get_reconciliation_result, get_case_evidence_snapshot, retrieve_approved_runbook_sections`. **Forbidden:** DB credentials/writes/SQL, filesystem/shell/code exec, open-web, RPA/booking-write/signing-key/secrets, policy/taxonomy/prompt publication, review creation/routing/approval/override/closure, cross-tenant/portfolio/case retrieval. Unknown tools + extra output fields fail closed.

## 14. Signed-Action Lifecycle (ADR-005/003/013 — normalised, consistency C-01/C-02)
**Canonical ordering (humans approve the exact executable payload, not a vague recommendation):**
1. LLM emits non-executable `proposed_resolution` (advisory).
2. Deterministic policy locks the allowed action type + field set + route.
3. Deterministic **compiler** builds the exact `ACTION_COMPILED` draft — exact expected-old + approved-new values, target booking version + required `CAS`|`LEASE`, scope + all consumed versions, nonce, expiry, idempotency key, **content hash** — **not signed, not executable**.
4. **Maker** approves the draft **content hash**; **distinct checker** approves the same hash. Any payload change → new draft → review restarts.
5. Only after both approvals → **sign** (asymmetric) → `ACTION_SIGNED`.
6. `DISPATCH_ELIGIBLE` (eligibility check #1) → Action Gateway publishes intent → eligibility check #2 immediately before the UI write → `ACTION_DISPATCHED`.
7. → `READBACK_PENDING | EXECUTION_UNCERTAIN` → `VERIFIED_APPLIED | VERIFIED_NOT_APPLIED | ESCALATED`. Terminal non-dispatch: `ACTION_CANCELLED / ACTION_REVOKED / ACTION_EXPIRED / ACTION_SUPERSEDED`; no terminal returns to dispatch.
**Sole MVP action: `SET_CONFIRMATION_REFERENCE`** (non-economic). Shared action/recommendation enum (C-10): `{SET_CONFIRMATION_REFERENCE, NO_ACTION, MANUAL_REVIEW}` — only the first compiles to an automated instruction. **Asymmetric signing only** (private key outside agent/executor); local file-backed test key for local-first, AWS KMS asymmetric CMK for the optional cloud path (ADR-008), same Gateway interface. Idempotency key binds tenant/portfolio/trade/action-type/target-booking-version/normalised old+new/draft-hash → **at-most-one semantic action per key** (never "exactly once").

## 15. Read-Back-Before-Write & Uncertain-Execution Behaviour (ADR-005/013/008)
Before any write, the executor obtains a fresh booking read-back and confirms target record, expected-old value, booking version, and unchanged non-target fingerprint. **Final-submit concurrency** requires the mock app to enforce either an optimistic version/CAS token or an expiring per-booking lease; a pre-read alone is insufficient; if neither exists, the write is disabled and the case is **manual-only**. Timeout/lost session/ambiguous save → `EXECUTION_UNCERTAIN`; recovery is **read-back-first** (never blind retry): authorised-new + clean reconciliation → `VERIFIED_APPLIED`; expected-old still present + still eligible → one bounded re-dispatch using the same idempotency key + fresh eligibility; any third value/version/read-back failure/expired authority → `ESCALATED`. Executor success alone never closes a case.

## 16. Audit & Evidence Model (ADR-012 — bounded claim)
**Permitted claim: "tamper-evident application evidence"** — detects unexpected mutation/deletion/reordering/missing-link within the evidence scope via an insert-only `audit_events` ledger with a **per-stream SHA-256 hash chain** (`event_hash = SHA-256(canonical_encoding_v1(header ‖ payload_hash ‖ prev_hash))`), content-addressed versioned artefacts, and an independent read-only `evidence_verifier` that gates case closure and release. **NOT claimed:** WORM, legal-hold, non-repudiation, absolute immutability, exactly-once, or regulatory compliance; a compromised privileged administrator is outside the guarantee (disclosed). Structured read-back + audit records are required; screenshots optional; redaction/persistence failure forces escalation, not closure. Per consistency item C-08, "immutable" is replaced project-wide with `deterministic non-reused ID` / `version-pinned tuple` / `content-addressed reference`.

## 17. Synthetic-Data & Scenario-Truth Strategy (ADR-006)
Deterministic versioned fixtures, one synthetic tenant + **≥2 portfolios**, `FX_SPOT`/`FX_FORWARD` only, owner-approved currency-pair config (no market-coverage claim). Four fixture contracts (FIX execution, FIX capture, FpML `fpml-style-fx-v1` confirmation, mock booking read-back); distinct `event_time`/`effective_time`/`ingest_time` + source sequence/version; deliberate late/revised/replay/missing/out-of-order variants. **Evaluator-only scenario-truth ledger** (latent cause → mutation → difference facts → break type) is never in features/prompts/RAG/traces. **Independent reconciliation oracle** (dependency-isolated from production reconciliation; CI import-isolation check). **Corpus scale (consistency C-07 — owner APPROVED 144):** **144 lifecycles = 48 clean + 96 mutated at 6 independently-parameterised mutations per (8 families × 2 products)**, covering FX Spot + Forward, break families, temporal variation, scenario-family separation, and reproducible ground truth. The count is a **contract-coverage target, not a model-training sample and not a completed dataset**. ADR-006's corpus arithmetic is updated to this binding value by Scout before E3 generator work. Two-run fixture-manifest reproducibility test required before "reproducible" is claimed.

## 18. AI Evaluation & Abstention Policy (ADR-007)
MVP evaluates only the constrained LLM. Outcomes: `RECOMMENDATION_READY | NEEDS_EVIDENCE | ABSTAIN | ESCALATE_SECURITY` (deterministic classifications of the structured output, not a competing schema). **Abstention triggers are deterministic, versioned validation signals** (schema invalid, missing/stale/contradictory evidence, unresolved source conflict, deterministic citation-support failure, category not permitted, injection/authority signal) — **self-reported LLM confidence is NOT a safety or release gate** (consistency SC-02). Citation validation is **deterministic fixture-authored expected-citation-ID matching** (no LLM-judge as a gate). Hard release thresholds (contract-suite, on the synthetic fixture corpus): structured-output validity 100%, citation correctness 100%, supported-recommendation 100%, required-abstention 100%, injection resistance 100%, unsafe-authority events 0, leakage 0, override-record completeness 100%; each fixture runs **3×**, a single failed invocation fails the gate. These are contract-suite thresholds, not general accuracy/reliability claims.

## 19. GitHub Repository & Engineering-Governance Model (ADR-009)
Monorepo **`tradeops-sentinel`**, **private** during build-out (deliberate later public flip). Trunk-based, protected `main` (no direct pushes incl. owner; linear history). Path-scoped CODEOWNERS; **2 independent reviewers** on signed-action / maker-checker / tool-registry / `infra/` paths. PR template (linked issue, AI-assisted disclosure field, "touches signed-action/DDL?" checkbox); issue templates (`bug_report, feature_request, adr_proposal, assurance_finding`); labels (`area:*`, `severity:*`, `type:*`, `status:*`); milestones per phase. SemVer; tags are the only path to `vX.Y.Z-rc.N`/`vX.Y.Z` (only from `main` after the ADR-010 RC gate); auto-generated `CHANGELOG.md`; ADRs in `docs/adr/`. **AI-authorship transparency:** `Generated-by:` trailer for the authoring agent alongside human `Signed-off-by`/`Co-authored-by`; **no synthetic commits/reviews**. **Owner-only merge + release authority enforced structurally** (CODEOWNERS + protected environments; agents get write not admin; PR authors can't self-approve).

## 20. CI/CD Stages & Release-Evidence Gates (ADR-010/014)
- **T0/T1 — every PR, local, no cloud/paid (runs now):** format/lint · type-check · unit · contract (Pydantic) · schema (FIX/FpML incl. malformed→reject) · reconciliation-invariant fixtures · action-signature + idempotency contract (mocked gateway/executor) · **LLM-eval via deterministic citation fixtures, 3× per fixture** · secret + dependency scan · container build · `terraform validate` · integration (state-resume, mocked maker/checker, cross-portfolio isolation) · **Playwright end-to-end action-safety + uncertain-execution drills against the mock app** · evidence/audit-integrity (verifier, disposable store) · evidence-manifest completeness.
- **T2 — release-candidate tag (needs owner-approved env):** T0/T1 green + pinned artefacts + independent evidence review + `release_evidence` hard-fail gate (unconditional list) + signed/scoped/expiring **exception record** check + deployment evidence only if the optional cloud path is used.
- **T3 — scheduled operating proof (post-MVP):** synthetic continuity, SLO/latency/cost, incident drill, postmortem.
**Sprint-1 CI clarification (consistency C-04):** Sprint 1 builds **gate structure + schemas + deterministic contract simulators + evidence-manifest generation only**. The LLM-eval and Playwright-e2e gates are **defined now, enabled when their implementation lands** (DoR satisfied); **no green placeholder counts as evidence.** Metric-regression comparisons stay disabled until owner approves ADR-007 baselines. Playwright evidence proves the executor safety pattern only — **never labelled UiPath or cloud proof** (consistency C-03/SC-01).

## 21. Local MVP Runtime
Single `docker-compose`: PostgreSQL 16 + pgvector, one application container, mock legacy booking web app, Playwright executor. No cloud account, no paid resource, no broker. This is the required path to prove the vertical slice.

## 22. Optional Cloud Reference Path
**Not required** to prove the MVP loop; exists for one demonstrated cloud path in portfolio evidence. AWS, single region (proposed `ap-southeast-2`, owner to confirm), **same** container images on one ECS Fargate service, RDS PostgreSQL (single small instance), versioned S3 for evidence (NOT Object Lock by default), Secrets Manager, CloudWatch + OTel, KMS asymmetric CMK for signing. No Multi-AZ, cross-region, or Kubernetes. Owner-gated; not part of Sprint 1.

## 23. Legacy Executor Strategy & UiPath Post-MVP Boundary (ADR-011)
MVP executor = **free deterministic Playwright** against the mock app, behind the Action Gateway interface — it exercises the exact read-back-before-write / uncertain-execution boundary the review targeted, headless on ordinary CI. The prior "~$420/mo" UiPath figure is **retracted** (unattended + serverless pricing are both quote-based/non-public per UiPath's own docs). Real UiPath is **post-MVP**, activated only on a decision-grade vendor quote, as an interface swap (Robot gets no AWS/DB credential; egress/proxy design deferred with it). Demo material must state the executor is Playwright, not UiPath.

## 24. Security & Secrets Model
Least privilege (agents write not admin; Action Gateway has its own DB role; `audit_events` insert-only with UPDATE/DELETE denied at grant level); asymmetric signing key outside agent/executor (local file-backed test key / cloud KMS CMK); secrets via `.env`+docker secrets (local) / Secrets Manager (cloud); CI→AWS via OIDC, no static keys; TLS in transit; LLM sees only redacted, same-tenant/case context; injection boundary is deterministic (metadata/template separation before the model; retrieved text is data-only). **At-rest encryption (consistency C-11):** claimed only in an environment where it is configured + evidenced (cloud RDS/S3); local at-rest encryption is a **known limitation / owner decision**, not an MVP claim.

## 25. Failure-Injection Matrix (ADR-013 — required tests)
| Scenario | Required assertion |
|---|---|
| Duplicate source event (same id/version, same content) | One semantic observation/result; duplicate is auditable |
| Same source id/version, different content | `DUPLICATE_SOURCE_CONFLICT` raised (C-09) |
| Duplicate action delivery | One disposition; no re-write |
| Stale approval / changed case version | Eligibility rejects dispatch; supersession recorded |
| Revoked approval after publish | Second eligibility check rejects the queued instruction |
| Queue cancellation race | Cancellation wins or both checks valid; no ambiguous silent closure |
| Concurrent booking change | Final-submit CAS/lease fails; no overwrite |
| UI timeout after apparent save | `EXECUTION_UNCERTAIN`, read-back-first, no blind retry |
| Read-back mismatch | Escalation with exact expected/actual evidence |
| Signature failure / revoked key | No dispatch; audited |
| Expired instruction | No dispatch or retry |
| Partial crash at each external boundary | Restart reaches a deterministic safe state; no duplicate semantic action |
All run in local docker-compose CI on every PR (deterministic mock-app selectors). UiPath-specific validation is post-MVP.

## 26. Release-Assurance & Independent-Sign-Off Policy (ADR-014)
Versioned evidence tuple + machine-readable `release_evidence` manifest (invalid if fields/artefacts missing). Tiers T0–T3 as §20. **Unconditional hard failures** (no threshold waiver): unauthorised/write-capable agent tool exec; policy/maker-checker bypass; invalid/replayed/expired/revoked instruction; reconciliation-invariant failure; unsafe injection outcome; missing/unsupported required citation; raw secret/sensitive value in prompt/trace; cross-case/portfolio leakage; duplicate semantic action; unsafe uncertain-execution handling; incomplete/tampered evidence chain; missing required artefact. **No self-certification** — a workstream owner cannot be the sole reviewer of their own model/agent/policy/infra/evidence gate; **Fizz recommends, Ozzy is sole final merge/release authority**, enforced structurally. Exceptions are signed/scoped/expiring machine-validated records and **cannot** waive a Critical/hard-fail condition.

## 27. Known Limitations & Claims That Must Not Be Made
Synthetic FX only; one tenant / ≥2 portfolios; 8 bounded break families; one non-economic automated field (economics manual-only); **tamper-evident ≠ WORM/legal-hold/immutable**; **at-most-once semantic action ≠ exactly-once**; no learned models / second LLM / similar-case retrieval / regulatory-extract source / Kubernetes / multi-region / live UiPath in MVP; tolerances/materiality are owner-config (no operational-threshold claim until set); at-rest encryption only where configured+evidenced (cloud); a compromised privileged admin is outside the tamper-evidence guarantee; the 144-lifecycle corpus proves contract coverage only, not model quality/market realism; the automated write depends on the mock app exposing a CAS/lease seam. **Forbidden claims:** "immutable", "exactly once", "secure/production-ready", "WORM/legal-hold", "regulatory-compliant", or any UiPath/cloud result the MVP did not actually run.

## 28. Cost & Licensing Position
**MVP is buildable at ~zero external cost** — local-first, free Playwright executor, no managed broker, no paid robot, no Windows harness. The retracted UiPath figure does not inform any budget. Optional cloud reference path (single region, one Postgres + one container) is owner-gated with a small ceiling; UiPath is post-MVP pending a vendor quote. Owner still sets: repo location, cloud region + monthly ceiling (optional path only), evidence-retention window.

## 29. Sprint 1 Backlog (foundations only)
See the detailed issue table below (§"Sprint 1 GitHub Projects Backlog"). Epics: **E1** Repository & engineering governance · **E2** Contracts, schemas & ADR traceability · **E3** Deterministic synthetic FX generator · **E4** Canonical trade-state persistence · **E5** Deterministic reconciliation engine · **E6** CI foundation & evidence manifest. **NOT in Sprint 1:** cloud deployment, UiPath execution, ML training, the LLM workflow, and the Playwright executor implementation (their CI gates are scaffolded, not enabled — C-04).

## 30. Definition of Ready (per issue)
Owner-approved parent ADR; business purpose + technical scope written; owner assigned; dependencies identified + unblocked; acceptance criteria + required tests + required evidence + explicit exclusions stated; **no unverifiable claim without a named mechanism + test**; touches-signed-action/DDL flag set; for a gate-enabling issue, the implementation it gates exists and passes on real (non-placeholder) output.

## 31. Definition of Done (per issue / Sprint)
Code behind a PR (no direct main); CODEOWNER review (2 on high-risk paths); all required T0/T1 gates green on real output (no placeholder pass); contract/invariant tests present + passing; evidence artefact produced + hash-referenced in the manifest; ADR traceability recorded (issue→PR→test→evidence); accurate status label (planned/implemented/locally-tested; cloud/operational deferred); `Generated-by:` + human `Signed-off-by`/`Co-authored-by` trailers present; negative-capability tests pass where applicable; Fizz independent check where the ADR requires it (oracle isolation, evidence verifier, no self-certification).

## 32. Owner Decisions Required
1. **Approve this charter + all 14 ADRs.**
2. Corpus scale — **APPROVED: 144** (48 clean + 96 mutated at 6 mutations/family/product); ADR-006 to be updated to match before E3 (C-07).
3. Final-submit control: build a **CAS/lease seam** in the mock app, or accept `SET_CONFIRMATION_REFERENCE` as manual-only for MVP (ADR-005/013).
4. GitHub org/account **location**; confirm **private** visibility + who authorises the later public flip; confirm the `Generated-by:` trailer format; map human GitHub identities to CODEOWNERS paths (ADR-009).
5. Source-of-truth precedence + deterministic linkage keys + decimal scales (ADR-001).
6. Break families + severities + arrival windows + tolerances + materiality bands + non-action dispositions; market-calendar in/out (ADR-002).
7. Maker/checker roles + separation + review expiry + manual-only economic fields + override dispositions + timeout/escalation owner (ADR-003).
8. Resolution/recommendation enum + runbook owners + tool/token/latency budgets + citation/abstention thresholds + 3× rule + any High-severity exception (ADR-004/007/014).
9. Evidence retention duration + classification/access + screenshot-vs-structured-only; confirm **no WORM claim**; **at-rest encryption** posture (C-11) (ADR-012).
10. Optional cloud reference path in/out for the MVP milestone + AWS region + monthly ceiling (ADR-008); confirm **UiPath deferred post-MVP** pending a quote (ADR-011).
11. Final release-approval authority = owner; minimum independent reviewer roles (ADR-014).

## 33. Final Implementation-Readiness Status
**READY WITH OWNER DECISIONS.** No unresolved cross-ADR contradiction remains after the normalisations in this charter (consistency C-01…C-12, SC-01…SC-03 all resolved-in-charter). Sprint 1 (E1–E6, foundations only) can begin immediately once the owner approves this charter + the 14 ADRs and rules the §32 decisions that gate foundations (repo location/monorepo; ADR-001/002/006 domain contracts; corpus scale). The LLM workflow, signed action + executor, and cloud/UiPath remain gated on their own decisions. Nothing is built until sign-off.

---

## Consistency Pass (Orchestrator, incorporating Honey/Fizz/Scout passes)
Verified across ADR-001…014: **no ADR contradicts another after normalisation; terminology, state names, action-contract fields, source-of-truth rules, evidence requirements, CI-gate→test mapping, and Sprint-1 exclusions all align; no unverified cost/security/immutability/exactly-once/resilience/production claim remains.** Items found and their charter resolution:

| ID | Sev | Item | Charter resolution (canonical) |
|---|---|---|---|
| C-01 | Crit | Action lifecycle ordering (ADR-005 prose vs ADR-003/013) | §14: humans approve the exact **compiled draft content hash**; sign only after approval; pre-review object is non-executable `proposed_resolution` |
| C-02 | High | Two instruction enums | §11/§14: single canonical `ACTION_*` enum from ADR-003 |
| C-03/SC-01 | High | UiPath-specific wording in ADR-012/013/014 | §16/§20/§23/§25: use "approved legacy executor / executor receipt"; Playwright = MVP; UiPath post-MVP; never label Playwright evidence as UiPath |
| C-04 | High | ADR-010 per-PR LLM+Playwright vs Sprint-1 exclusion | §20/§29: Sprint 1 = gate structure + schemas + simulators + manifest; LLM/Playwright gates defined-not-enabled; no placeholder pass counts |
| C-05 | High | ADR-008 in-process ML module vs deferred ML | §6: **remove learned ML from MVP runtime**; post-MVP typed seam only, no artefact/gate |
| C-06 | High | Tenant/portfolio count (ADR-001 vs ADR-006) | §8/§17: one tenant + **≥2 portfolios**; one case = one trade = one portfolio; cross-portfolio negatives |
| C-07 | Med | Corpus arithmetic (96 vs 144) | §17/§32: 96 minimum at 3 mutations/family/product, or owner approves 144 |
| C-08 | Med | "immutable" wording vs claim boundary | §16/§27: replace with `deterministic non-reused ID`/`version-pinned tuple`/`content-addressed reference` |
| C-09 | High | Dedup key vs duplicate-conflict detection | §7/§10/§25: unique index on source **identity/version**; content_hash separate; same id/version + diff content → `DUPLICATE_SOURCE_CONFLICT` |
| C-10 | Med | Action/recommendation enum mismatch | §14: shared `{SET_CONFIRMATION_REFERENCE, NO_ACTION, MANUAL_REVIEW}`; only first compiles |
| C-11 | Med | At-rest encryption unspecified locally | §24/§27/§32: known limitation; claimed only where configured+evidenced (cloud) |
| C-12 | Low | ADR frontmatter `status: proposed` | Editorial: ADRs use `status: draft` in YAML (Epic 2 cleanup) |
| SC-02 | Med | "low confidence" abstention trigger | §18: abstention is deterministic validation signals only; self-reported confidence is not a gate |

**Unresolved conflicts: NONE.** All items above are resolved by this charter's canonical wording; the affected ADRs conform during Sprint 1 Epic 2 (contracts). The remaining open items are **owner decisions (§32)**, not conflicts.

---

## Repository-Creation Checklist (prepared; DO NOT create the repo yet)
| Item | Recommendation |
|---|---|
| Repository name | `tradeops-sentinel` |
| Visibility | **private** during build-out; public flip is a later reviewed decision |
| Org/account location | **owner decision** |
| Structure | monorepo (§13 layout: apps/, packages/{contracts,generator,reconciliation,evidence,executor}, docs/adr, infra/, tests/, .github/) |
| README skeleton | project statement, synthetic-only + non-production disclaimer, architecture diagram link, "claims that must not be made" (§27), status labels, quickstart (local docker-compose) |
| LICENSE | permissive **MIT** or **Apache-2.0** (owner to pick; Apache-2.0 if patent grant preferred) |
| SECURITY.md | synthetic-data-only scope; no real secrets; how to report; signed-action/maker-checker as security-critical paths; no live UiPath/cloud in MVP |
| CONTRIBUTING.md | trunk-based flow, PR + CODEOWNERS rules, ADR process, **AI-authorship `Generated-by:` trailer + human Signed-off-by/Co-authored-by**, no synthetic commits/reviews |
| CODEOWNERS | path-scoped per ADR-009 (2 reviewers on signed-action/maker-checker/tool-registry/infra); `docs/adr/` → orchestrator + owner |
| PR template | linked issue, what/why, checklist (tests added, ADR needed?, touches signed-action/DDL?), AI-assisted disclosure field |
| Issue templates | `bug_report`, `feature_request`, `adr_proposal`, `assurance_finding` |
| Branch protection | protected `main`: no direct pushes (incl. owner), linear history, required reviews, required status checks, no self-approval |
| Required status checks | T0/T1 local gates (§20) that exist in Sprint 1 (lint/type/unit/contract/schema/reconciliation-invariant/action-signature+idempotency-sim/secret+dep/manifest-completeness) |
| GitHub Projects board | columns Backlog → In Progress → In Review → Blocked/Needs Owner Decision → Done |
| Milestones | one per epic (E1–E6), then later sprints |
| Labels | `area:*`, `severity:*`, `type:*`, `status:*` |
| Release convention | SemVer; `vX.Y.Z-rc.N` / `vX.Y.Z` from `main` only after the RC gate; auto CHANGELOG |
| AI-contribution disclosure | `Generated-by:` trailer + PR disclosure field; owner-only merge/release enforced structurally |

## Sprint 1 GitHub Projects Backlog (proposed; not created)
*(Each issue: title · business purpose · technical scope · owner · dependencies · acceptance criteria · required tests · required evidence · exclusions · DoD mapping. Condensed here; DoD maps to §31.)*

**E1 Repository & engineering governance** (Bumble)
- **TS-1 Monorepo skeleton + branch protection + CODEOWNERS + templates.** Purpose: enforce governance before code. Scope: repo layout, protected main, CODEOWNERS, PR/issue templates, labels, milestones, LICENSE/SECURITY/CONTRIBUTING. Deps: §32.4 owner decisions. Acceptance: direct push to main blocked; CODEOWNER review required; templates resolve. Tests: PR cannot merge without CODEOWNER approval. Evidence: exported branch-protection settings. Exclusions: no app code. DoD: §31.
- **TS-2 CI skeleton (T0) + AI-authorship trailer check.** Scope: lint/type/unit workflow, `Generated-by:` trailer linter. Acceptance: green on a no-op PR; trailer-missing PR fails. Evidence: workflow run logs.

**E2 Contracts, schemas & ADR traceability** (Honey lead + Bumble)
- **TS-3 Canonical FX model + observation schemas** (ADR-001). Acceptance: decimal/orientation/side fixtures pass; provenance fields present. Tests: schema-validation + replay determinism. Deps: §32.5.
- **TS-4 Break-record + taxonomy schema** (ADR-002, 8 families). Acceptance: break record carries rule/version/evidence. Deps: §32.6.
- **TS-5 Evidence + signed-action **contract** schema (non-executable draft + hash)** (ADR-005/012). Acceptance: payload fields present; content-hash computed; **no signing/dispatch code**. Exclusions: no executor.
- **TS-6 ADR frontmatter cleanup to `status: draft`** (C-12) + ADR→issue traceability links.

**E3 Deterministic synthetic FX generator** (Scout)
- **TS-7 Coherent Spot/Forward lifecycles** (ADR-006). Acceptance: generator rejects incoherent baselines (ADR-001 arithmetic). Tests: baseline-invariant.
- **TS-8 Injected mutations per approved family/product + evaluator-only scenario-truth ledger.** Acceptance: corpus meets approved scale (§32.2); truth-leakage scan clean. Tests: truth-field leakage scan across features/prompts/traces.
- **TS-9 Two-run reproducibility + manifest.** Acceptance: identical content hashes across two isolated runs.

**E4 Canonical trade-state persistence** (Honey + Bumble)
- **TS-10 Canonical assembler + versioned projection + provenance + source_event_inbox (unique on identity/version, content_hash separate — C-09).** Acceptance: late-arrival/correction preserve prior versions; same-id/diff-content flagged for reconciliation. Tests: replay/late-arrival/duplicate-vs-conflict.

**E5 Deterministic reconciliation engine** (Honey + Bumble; Fizz oracle review)
- **TS-11 8 break-family rules + tolerances-as-config.** Acceptance: positive/boundary/negative fixture per rule (Spot+Forward). Tests: reconciliation-invariant suite.
- **TS-12 Independent oracle + CI import-isolation check.** Acceptance: oracle imports no production reconciliation code; Fizz reviews. Evidence: dependency-graph check output.
- **TS-13 Rerun determinism + duplicate-conflict invariants.** Acceptance: identical result on locked inputs; no duplicate breaks/cases.

**E6 CI foundation & evidence manifest** (Bumble + Fizz)
- **TS-14 Hash-chain audit ledger (insert-only role) + evidence_verifier.** Acceptance: runtime UPDATE/DELETE denied; verifier detects payload/predecessor/reorder tamper on a disposable store. Tests: ADR-012 tamper matrix.
- **TS-15 `release_evidence` manifest generation + completeness gate + reconciliation-invariant gate wired to PR tier.** Acceptance: manifest invalid if fields/artefacts missing. Exclusions: no RC hard-fail gate run (needs later env); **LLM-eval + Playwright-e2e gate definitions scaffolded but disabled (C-04) — no placeholder green.**

## Confirmation
**No repository, implementation code, cloud resource, paid service, UiPath execution, model training, or deployment has started.** All 14 ADRs and this charter are proposals awaiting owner sign-off. Design-stage only.
