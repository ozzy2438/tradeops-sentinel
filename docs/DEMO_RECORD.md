# TradeOps Sentinel — end-to-end demonstration record

Captured from a live run of the product against a disposable PostgreSQL 18
instance. Commands are verbatim; the API key shown is a local throwaway value
and no real credential appears anywhere in this file.

This transcript deliberately runs reconciliation **twice** (a demonstration
of `fix/latest-run-visible-results`): `reconciliation_runs` and `trade_breaks`
are append-only, so two runs leave two historical runs and 180 historical
break rows in the database -- but every default-scoped view (`/summary`, the
default `/breaks`, and everything the dashboard renders) shows only the
**latest** run's 90. Full history stays reachable via `/runs` and
`/breaks?run_id=<historical-run>`.

```
$ curl -s http://localhost:8000/health
{"status":"ok","service":"tradeops-sentinel-api"}

$ curl -s http://localhost:8000/ready
{"ready":true,"detail":"ready"}

$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/summary   # no API key
401

$ curl -s -X POST -H 'X-API-Key: ***' http://localhost:8000/demo/load
{"inserted":570,"replayed":0,"conflicted":12,"total":582,"tenant_id":"tenant_demo","portfolio_ids":["portfolio_london","portfolio_sydney"]}

$ curl -s -X POST -H 'X-API-Key: ***' http://localhost:8000/reconciliation/run   # run #1
{"run_id":"run_20260804063536","trades_evaluated":144,"clean_trades":54,"broken_trades":90,"break_count":90,"config_hash":"sha256:871803f9a9210fb84077d485a27fd5a647327731ea10dfb667cf5281935215ba"}

$ sleep 1.5

$ curl -s -X POST -H 'X-API-Key: ***' http://localhost:8000/reconciliation/run   # run #2, >=1s later
{"run_id":"run_20260804063538","trades_evaluated":144,"clean_trades":54,"broken_trades":90,"break_count":90,"config_hash":"sha256:871803f9a9210fb84077d485a27fd5a647327731ea10dfb667cf5281935215ba"}
```

## Default view after two runs — shows only the latest run's 90

```
$ curl -s -H 'X-API-Key: ***' http://localhost:8000/summary
{
    "tenant_id": "tenant_demo",
    "portfolio_ids": ["portfolio_london", "portfolio_sydney"],
    "total_observations": 570,
    "total_trades": 144,
    "clean_trades": 54,
    "broken_trades": 90,
    "total_breaks": 90,
    "breaks_by_family": {
        "TRADE_OR_VALUE_DATE_MISMATCH": 12,
        "DUPLICATE_SOURCE_CONFLICT": 12,
        "MISSING_REQUIRED_SOURCE": 12,
        "CURRENCY_PAIR_OR_SIDE_MISMATCH": 12,
        "ECONOMIC_VALUE_MISMATCH": 12,
        "AMBIGUOUS_OR_UNMATCHED_LINKAGE": 12,
        "LIFECYCLE_STATUS_MISMATCH": 18
    },
    "trades_by_product": {"FX_FORWARD": 72, "FX_SPOT": 72},
    "latest_run_id": "run_20260804063538",
    "latest_run_completed_at": "2026-08-04T16:35:38.842025+10:00",
    "config_hash": "sha256:871803f9a9210fb84077d485a27fd5a647327731ea10dfb667cf5281935215ba"
}

$ curl -s -H 'X-API-Key: ***' 'http://localhost:8000/breaks?limit=500' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
90
```

## Full history is still there — `GET /runs` returns both

```
$ curl -s -H 'X-API-Key: ***' http://localhost:8000/runs
[
  {"run_id":"run_20260804063538","portfolio_id":"portfolio_sydney","trades_evaluated":72,"broken_trades":45,"break_count":45,"status":"COMPLETED","completed_at":"2026-08-04T16:35:38.842025+10:00"},
  {"run_id":"run_20260804063538","portfolio_id":"portfolio_london","trades_evaluated":72,"broken_trades":45,"break_count":45,"status":"COMPLETED","completed_at":"2026-08-04T16:35:38.771372+10:00"},
  {"run_id":"run_20260804063536","portfolio_id":"portfolio_sydney","trades_evaluated":72,"broken_trades":45,"break_count":45,"status":"COMPLETED","completed_at":"2026-08-04T16:35:36.577122+10:00"},
  {"run_id":"run_20260804063536","portfolio_id":"portfolio_london","trades_evaluated":72,"broken_trades":45,"break_count":45,"status":"COMPLETED","completed_at":"2026-08-04T16:35:36.563568+10:00"}
]
```

Four rows: one per portfolio, per run — two runs, never collapsed or overwritten.

## Each historical run is still individually inspectable — 90 each, disjoint

```
$ curl -s -H 'X-API-Key: ***' 'http://localhost:8000/breaks?run_id=run_20260804063536&limit=500' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
90

$ curl -s -H 'X-API-Key: ***' 'http://localhost:8000/breaks?run_id=run_20260804063538&limit=500' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
90
```

## Break detail — provenance and expected/observed evidence

```
$ curl -s -H 'X-API-Key: ***' http://localhost:8000/breaks/break_lifecycle_status_mismatch_cfbf11d6…
{
  "break_id": "break_lifecycle_status_mismatch_cfbf11d6…",
  "trade_id": "trade_349b28d47c4d…",
  "product_type": "FX_SPOT",
  "break_family": "LIFECYCLE_STATUS_MISMATCH",
  "severity": "HIGH",
  "state": "OPEN",
  "condition_code": "ALLOWED_LIFECYCLE_RELATION",
  "config_hash": "sha256:871803f9a9210fb84077d485a27fd5a647327731ea10dfb667cf5281935215ba",
  "detection_rule_version": "1.0.0",
  "run_id": "run_20260804063538",
  "comparisons": [
    {
      "field_path": "/payload/lifecycle_status",
      "value_type": "LIFECYCLE_STATUS",
      "expected_value": "NEW",
      "observed_value": "CANCELLED",
      "expected_source_version": "1",
      "observed_source_version": "1",
      "expected_source_observation_id": "obs_execution_e7845be74ab9b93778cb9a0ee331f579",
      "observed_source_observation_id": "obs_booking_e12e0e21a7c848d782e2ff2430425e3f"
    }
  ],
  "source_version_set": [
    {
      "source_system": "FIX_EXECUTION",
      "source_version": "1",
      "observation_kind": "EXECUTION",
      "source_tenant_id": "tenant_demo",
      "source_portfolio_id": "portfolio_sydney",
      "source_observation_id": "obs_execution_e7845be74ab9b93778cb9a0ee331f579",
      "content_hash": "sha256:6174688ac6c4941ca046d380763f1b73f79d4310c2cc11fad968edae3d541cd3"
    },
    {
      "source_system": "FIX_TRADE_CAPTURE",
      "source_version": "1",
      "observation_kind": "TRADE_CAPTURE",
      "source_tenant_id": "tenant_demo",
      "source_portfolio_id": "portfolio_sydney",
      "source_observation_id": "obs_trade_capture_bc127d53897f9c3d128fb3fa2efba5d9",
      "content_hash": "sha256:00e550716b800232de6f13832c7cab7cd20092e9aa60a05efc50aac5ee4a005e"
    }
  ]
}
```
