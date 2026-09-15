---
name: testing-qa
description: Defines unit, integration, regression, replay, security, and acceptance tests for direction-engineV3. Use when implementing behavior, fixing bugs, changing interfaces, or declaring a phase complete.
---

# Testing and QA

Tests are evidence, not ceremony.

## Test layers

### Unit

Use for deterministic math/state:

- PTB/TTE;
- fee math;
- VWAP;
- edge;
- calibration helpers;
- strategy gates;
- structural parity;
- risk;
- state transitions.

### Integration

Use for boundaries/pipelines:

- discovery → market contracts;
- feeds → normalized store;
- official/proxy reference routing;
- model → strategy decision;
- risk → execution plan;
- gateway → reconciliation;
- persistence migrations.

### Regression

Every fixed trading-critical bug should gain a regression test where practical.

High-priority regressions:

- wrong TTE;
- wrong outcome/token mapping;
- stale book accepted;
- source timestamp confused with receive time;
- official/proxy substitution;
- fee omitted;
- partial fill treated as full;
- order ACK treated as fill;
- duplicate submit after timeout/restart;
- one-leg residual exposure;
- LIVE enabled by default.

### Replay

Use deterministic historical/reconstructed cases for latency and execution behavior.

## Test quality

Do not:

- mock away the behavior under test;
- assert only that code "does not crash";
- loosen expected values to hide a bug;
- mutate environment globally across tests without restoration;
- make network-dependent tests masquerade as unit tests.

## Completion gate

Before phase completion run the relevant subset of:

- compile/syntax;
- unit;
- integration;
- regression;
- replay;
- lint/type;
- smoke.

Record exact failures.
