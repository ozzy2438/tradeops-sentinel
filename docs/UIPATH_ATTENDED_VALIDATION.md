# UiPath attended live-validation record

## Result

**Status: pending one UiPath Studio Web attended debug run on 2026-08-08.**

The official UiPath Studio Web Chrome extension is enabled and macOS
Accessibility is enabled for UiPath Assistant. The repository implementation
and PostgreSQL end-to-end boundary are already validated locally. This record
must not be changed to `live-validated` until UiPath Studio Web/Assistant
itself clicks the target and the resulting append-only events and post-action
reconciliation are read back.

## Intended bounded run

- product: UiPath Community;
- authoring: UiPath Studio Web;
- execution: UiPath Assistant on an Apple Silicon macOS machine;
- mode: `ATTENDED_COMMUNITY`, manually triggered;
- workflow: `TradeOps Sentinel Attended Executor`;
- target: local synthetic `Mock Legacy Booking` HTML screen;
- action: click `Apply approved correction` for one approved
  `/payload/base_amount` change;
- AI mode during this run: deterministic (no Azure OpenAI request);
- evidence: `PREPARED` → `STARTED` → `COMPLETED`, exactly one applied write,
  read-back `1018000.00`, and post-action reconciliation `PASS`.

The one-time launch token is deliberately omitted. No account identity,
credential, signing secret, database connection string or Azure identifier is
stored in this record.

## Automated evidence already passing

The PostgreSQL integration test proves that the browser boundary:

- refuses preparation before Maker and Checker approvals;
- rejects an incorrect launch token;
- executes a valid signed action once;
- returns `DUPLICATE_NOOP` on replay;
- records only one applied completion;
- excludes the raw token from case evidence; and
- produces post-action reconciliation `PASS`.

Reproduce against a disposable PostgreSQL 16 database with:

```bash
TRADEOPS_TEST_DATABASE_URL=postgresql://user:password@localhost/tradeops_test \
  pytest -q tests/integration/test_uipath_attended_e2e.py
```

## Claim boundary

Until the result above is changed with actual run evidence, only the code and
automated boundary may be claimed. After a successful attended run, the record
will prove one UiPath Community execution against synthetic data. It will not
prove unattended Orchestrator dispatch, serverless execution, production
scheduling, operational resilience, a real legacy-system integration, or a
production banking write.
