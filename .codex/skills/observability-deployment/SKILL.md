---
name: observability-deployment
description: Defines structured logs, metrics, health checks, audit trails, Windows development, Linux/systemd deployment, restart safety, and production diagnostics for direction-engineV3.
---

# Observability and Deployment

## Structured decision audit

Each decision should be traceable with fields such as:

- market/condition/combo key;
- asset/horizon;
- TTE;
- PTB + source/freshness;
- official/proxy source ages;
- model/artifact version;
- raw/calibrated probability;
- selected side;
- executable price/VWAP;
- fee/slippage;
- net edge;
- strategy gate results;
- risk result;
- execution-plan ID;
- final order/position/outcome.

Do not log secrets.

## Metrics

Track:

- feed connection status;
- source freshness/age;
- reconnect count;
- sequence gaps;
- book coverage;
- decision count;
- ABSTAIN reasons;
- strategy candidate count;
- risk rejection reasons;
- order submit/ack/reconcile latency;
- partial/unknown/one-leg events;
- paper/live PnL;
- model calibration/quality summaries.

## Health

Separate:

- process alive;
- feed connected;
- data fresh;
- database writable/readable;
- model artifact loaded;
- trading readiness.

Do not return "healthy" solely because HTTP responds.

## Deployment

Development may run on Windows.
Production services should have:

- explicit working directory;
- isolated environment;
- restart policy;
- graceful shutdown;
- log rotation;
- persistent data path;
- migration step;
- health/status command;
- rollback procedure.

A restart must not duplicate orders or silently arm LIVE.

## Database

Use migrations.
Back up persistent trade/ledger/model metadata before destructive schema changes.
