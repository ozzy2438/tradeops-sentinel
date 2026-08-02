# Architecture Decision Records — Index

All 14 ADRs approved as the Sprint 1 implementation baseline (owner approval: "Create the GitHub repository and commence Sprint 1", 2026-07-31). Full narrative context lives in `../CHARTER_REFERENCE.md` (the MVP Release Charter). ADR frontmatter cleanup (`status: proposed` → `status: draft`, consistency item C-12) was applied by Epic E2 issue **TS-6**; all 14 ADRs now carry `status: draft` and this index is current with the frontmatter.

| ADR | Title | Owner | Implemented by | Closes |
|---|---|---|---|---|
| [ADR-001](ADR-001_CANONICAL_FX_TRADE_MODEL_AND_SOURCE_OF_TRUTH_PRECEDENCE.md) | Canonical FX Trade Model & Source-of-Truth Precedence | Honey | #3 | Grain/feature-snapshot + source-of-truth review findings |
| [ADR-002](ADR-002_DETERMINISTIC_TRADE_BREAK_TAXONOMY.md) | Deterministic Trade-Break Taxonomy | Honey | #4 | 8 MVP break families, causal-vs-symptom seam |
| [ADR-003](ADR-003_WORKFLOW_STATE_MACHINE_AND_HUMAN_CONTROL_BOUNDARY.md) | Workflow State Machine & Human-Control Boundary | Honey | — | Deterministic human-review routing, maker-checker |
| [ADR-004](ADR-004_AGENT_AND_TOOL_AUTHORITY_MODEL.md) | Agent & Tool Authority Model | Honey | — | One constrained LLM, read-only tools, no agent-callable action-draft |
| [ADR-005](ADR-005_SIGNED_ACTION_INSTRUCTION_AND_VERIFICATION_CONTRACT.md) | Signed Action Instruction & Verification Contract | Honey | #5 | Critical action-boundary cluster |
| [ADR-006](ADR-006_SYNTHETIC_DATA_SCENARIO_TRUTH_AND_LEAKAGE_CONTROLS.md) | Synthetic Data, Scenario Truth & Leakage Controls | Scout | — | Independent oracle, temporal/scenario-family holdout |
| [ADR-007](ADR-007_MVP_AI_EVALUATION_AND_ABSTENTION_POLICY.md) | MVP AI Evaluation & Abstention Policy | Scout | — | Deterministic citation validation, no self-certifying LLM judge |
| [ADR-008](ADR-008_MVP_RUNTIME_AND_DEPLOYMENT_ARCHITECTURE.md) | MVP Runtime & Deployment Architecture | Bumble | — | Dropped Kinesis/Redpanda/Lambda; Action Gateway; local-first + optional cloud path |
| [ADR-009](ADR-009_GITHUB_REPOSITORY_AND_ENGINEERING_GOVERNANCE.md) | GitHub Repository & Engineering Governance | Bumble | #1 | This repository's governance model (Epic E1) |
| [ADR-010](ADR-010_CICD_QUALITY_AND_EVIDENCE_GATES.md) | CI/CD Quality & Evidence Gates | Bumble | #2 | Tiered gates (T0/T1/T2/T3), no placeholder-pass claims |
| [ADR-011](ADR-011_LEGACY_AUTOMATION_RUNTIME_AND_COST_DECISION.md) | Legacy Automation Runtime & Cost Decision | Bumble | — | Playwright MVP executor; UiPath post-MVP; retracted unverified cost figure |
| [ADR-012](ADR-012_TAMPER_EVIDENT_AUDIT_AND_EVIDENCE_POLICY.md) | Tamper-Evident Audit & Evidence Policy | Fizz | #5 | Bounded evidence claim (never WORM/immutable) |
| [ADR-013](ADR-013_FAILURE_REPLAY_REVOCATION_AND_UNCERTAIN_EXECUTION_SAFETY.md) | Failure, Replay, Revocation & Uncertain-Execution Safety | Fizz | — | Required failure-injection matrix |
| [ADR-014](ADR-014_RELEASE_ASSURANCE_AND_INDEPENDENT_SIGN_OFF.md) | Release Assurance & Independent Sign-Off | Fizz | — | No-self-certification release gates |

## Traceability

Every Sprint 1 issue references the ADR(s) it implements or depends on (see the issue templates and the Sprint 1 backlog in `../CHARTER_REFERENCE.md` §29). The chain is: **ADR → issue → PR → test → evidence artefact → release tag**, per Definition of Done (`../CHARTER_REFERENCE.md` §31).
