---
title: "ADR-015 — Attended UiPath Community Portfolio Validation"
tags: [tradeops-sentinel, adr, uipath, rpa, attended, portfolio]
status: implemented
created: 2026-08-08
---

# ADR-015 — Attended UiPath Community Portfolio Validation

## Status

Implemented as a post-MVP portfolio extension. Live execution evidence is
bounded to the environment and run recorded in
`docs/UIPATH_ATTENDED_VALIDATION.md`.

## Context

ADR-011 selected a deterministic local substitute for the original MVP and
deferred paid UiPath execution because unattended and serverless pricing was
not decision-grade. The later portfolio goal requires direct evidence of
UiPath integration while keeping new paid usage at zero and preserving the
existing human-control and signed-action boundaries.

UiPath Community on the owner's macOS machine provides an attended robot. It
does not provide evidence for unattended scheduling, serverless execution, a
Windows robot host, or production Orchestrator operations.

## Decision

Implement one manually triggered attended path using UiPath Studio Web and
UiPath Assistant against the existing synthetic mock legacy booking surface.

- The operator creates the case and supplies distinct Maker and Checker
  approvals through the authenticated product API.
- The server issues/reuses the signed action envelope, then returns a
  high-entropy launch URL valid for no more than 15 minutes.
- Only the token digest is persisted. The UiPath workflow receives no API key,
  signing secret, database credential, Azure credential, or Azure OpenAI key.
- UiPath opens the local page and clicks one stable target,
  `Apply approved correction`.
- The server independently re-verifies the launch token, expiry, approvals,
  envelope, allow-list, expected old value and idempotency key before writing.
- The same row-locked adapter performs the correction, reads it back and
  records append-only `PREPARED`, `STARTED` and `COMPLETED` evidence. A real
  write triggers scoped post-action reconciliation.
- Deterministic provider mode is used for this UiPath validation. The already
  validated Azure recommendation is not called again, so the UiPath run adds
  no Azure token cost.

## Consequences

The project may claim one real attended UiPath Community execution against a
synthetic mock legacy application after the validation record shows a
successful run. It must not claim unattended dispatch, zero-touch production
deployment, a real banking-system write, exactly-once delivery, or production
readiness.

The launch URL is an ephemeral bearer link and may appear in local browser
history. `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, short expiry
and digest-only persistence reduce exposure; they do not turn the link into a
general authentication system. Production use would require enterprise
identity, managed dispatch, network controls and secrets governance.

## Relationship to ADR-011

ADR-011 remains the historical MVP runtime and cost decision. This ADR does
not replace its warning about unverified unattended pricing and does not adopt
unattended UiPath. It authorises only the later, no-new-cost, manually triggered
portfolio validation described above.

## Sources

- [UiPath Studio Web — Local setup for RPA workflow and app projects](https://docs.uipath.com/studio-web/automation-cloud/latest/user-guide/local-setup-for-rpa-workflow-and-app-projects)
- [UiPath UI Automation — macOS configuration steps](https://docs.uipath.com/activities/other/latest/ui-automation/macos-configuration-steps)
- [UiPath Studio Web — Running a project](https://docs.uipath.com/studio-web/automation-cloud/latest/user-guide/running-a-project)
