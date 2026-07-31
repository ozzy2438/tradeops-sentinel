---
title: "ADR-004 — Agent and Tool Authority Model"
tags: [tradeops-sentinel, adr, llm, tools, rag, authority]
status: draft
created: 2026-07-31
---

# ADR-004 — Agent and Tool Authority Model

## Status

Proposed. Requires owner approval before implementation and threshold alignment
with ADR-007.

## Context

The MVP needs cited investigation and remediation guidance, not autonomous
decision-making. A second verifier LLM, similar-case retrieval, agent-created
review routing and agent-generated action instructions add authority and
evaluation burden without being necessary for the first vertical slice.

## Decision

### 1. One constrained LLM workflow

The MVP has one LLM workflow, `investigate_and_recommend`, which:

1. receives one frozen case-evidence snapshot for one trade/portfolio;
2. retrieves only owner-approved, versioned synthetic runbooks;
3. explains the deterministic break;
4. proposes a bounded resolution type; and
5. cites every material factual or procedural claim.

Deterministic services perform schema, provenance, citation, policy and action
validation. A second LLM verifier, learned classifier, learned priority,
similar-case retrieval and standalone RAG service are post-MVP.

### 2. Structured output

The required output is a versioned `InvestigationRecommendation` containing:

- case, trade, break and evidence-snapshot versions;
- `outcome` from `RECOMMENDATION_READY`, `NEEDS_EVIDENCE`, `ABSTAIN` or
  `ESCALATE_SECURITY`, aligned with ADR-007;
- a typed `outcome_reason`;
- `summary`;
- zero or more `finding` records with claim type and evidence IDs;
- `proposed_resolution_type` from an owner-approved enum;
- referenced canonical field paths, without executable exact old/new values;
- runbook step citations and applicability conditions;
- uncertainties, missing evidence and contradictions;
- prompt/model/config versions and correlation ID.

The LLM cannot set severity, priority, materiality, approver role, action field
values, signature, expiry or dispatch status. A deterministic compiler in
ADR-005 constructs an action draft only after policy locks the allowed action
type and field set.

### 3. Read-only tool allow-list

| Tool | Scope | Key controls |
| --- | --- | --- |
| `get_canonical_trade_state` | Exact case trade/version | Read-only, same tenant/portfolio, bounded response. |
| `get_source_observations` | Exact observation IDs already linked to the case | Read-only, version-bound, raw artefact excluded unless explicitly allowed. |
| `get_reconciliation_result` | Exact reconciliation and break versions | Read-only deterministic output. |
| `get_case_evidence_snapshot` | Frozen same-case evidence manifest | Read-only; no cross-case search. |
| `retrieve_approved_runbook_sections` | Approved corpus, effective version and permitted break family | Metadata-filtered retrieval; bounded top-k; returned text treated as untrusted data. |

The agent does not receive tools that request review, draft/compile/sign/cancel
or dispatch actions. Workflow and policy services invoke deterministic commands
outside the agent boundary.

### 4. Forbidden authority

The LLM and its tool runtime have no:

- generic database credentials, database writes or arbitrary SQL;
- filesystem/shell/code execution;
- open-web or unrestricted network retrieval;
- executor, booking-write, signing-key or secrets access;
- policy/taxonomy/prompt publication capability;
- review creation/routing, approval, override or case-closure authority; or
- cross-tenant, cross-portfolio or cross-case retrieval.

Each tool has a typed schema, caller/case scope, timeout, result-size limit,
rate limit and auditable invocation. Unknown tools and extra output fields fail
closed.

### 5. Retrieval and injection boundary

- Runbook ingestion is separate from case execution and requires owner-approved
  document ID, version, effective dates, break-family tags and content hash.
- Metadata filtering happens before vector/keyword retrieval. Retrieved content
  is rendered in a data-only envelope with explicit delimiters; instructions
  within it have no authority.
- The MVP may implement retrieval in-process; no standalone RAG microservice is
  justified.
- Any injection indicator, document-scope mismatch or unavailable provenance
  causes abstention for a material recommendation.

### 6. Deterministic validation and citations

The validator checks:

- schema and enum validity;
- case/trade/version scope;
- citation existence, approved source/version and effectivity;
- source-field claims against structured evidence;
- runbook-step IDs and applicability metadata;
- absence of forbidden instruction/action fields; and
- evidence completeness required by policy.

Release fixtures define expected citation-ID sets and supported recommendation
labels. An LLM judge, if later used for narrative quality, is advisory and
cannot be the sole release or action gate.

### 7. Abstention and failure

The workflow must abstain on missing/stale/contradictory evidence, unsupported
break/resolution type, citation failure, injection signal, scope mismatch,
tool failure, timeout, token/step budget exhaustion or low confidence under
ADR-007. Abstention routes deterministically to human investigation or more
evidence; it never defaults to action.

## Required tests and evidence

- Tool-contract and negative-authority tests enumerate every registered tool
  and prove forbidden capabilities are absent.
- Cross-case/portfolio/tenant and stale-version calls are rejected.
- Direct and indirect prompt-injection fixtures cannot alter tools, policy,
  routing or output schema.
- Unsupported/missing citations and extra action fields force abstention.
- Timeout/rate/size limits produce typed, auditable failures.
- Fixture-authored expected citation IDs and resolution labels gate release;
  no LLM self-certification.

## Consequences

- One LLM keeps the central AI value while reducing cost, latency and correlated
  model failure.
- Deterministic validation cannot judge every narrative nuance, so the MVP
  limits claims to structured evidence and approved runbook steps.
- Similar-case retrieval and richer narrative verification are explicitly
  deferred.

## Owner decisions required

1. Approve the one-LLM architecture and deferral of the second verifier LLM.
2. Approve the `proposed_resolution_type` enum and approved runbook owners.
3. Approve tool, token, latency and retry budgets.
4. Approve the abstention and citation thresholds defined with ADR-007.

## Review findings closed

Closes Fizz-on-Honey M-01/M-02/M-06/L-01 and Bumble-on-Fizz's
citation-self-certification finding.
