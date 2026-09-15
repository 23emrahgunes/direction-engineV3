---
name: strategy-router
description: Coordinates competing Directional Edge, Structural Arbitrage, and optional DUAL40 opportunities under shared capital and execution locks. Use when multiple strategies can act on the same market or bankroll.
---

# Strategy Opportunity Router

Multiple strategies may observe the same market, but they must not independently race to spend the same capital or mutate the same condition state.

## Inputs

Each strategy emits a candidate with:

- strategy/version;
- condition/market;
- required capital;
- expected/net edge or net profit;
- confidence/quality;
- candidate expiry;
- execution complexity;
- risk tags;
- compatible/incompatible concurrent positions.

## Locks

Support explicit locks/claims for:

- condition/market;
- outcome token when needed;
- bankroll/exposure reservation;
- structural two-leg cycle.

Claims must expire/reconcile safely after process failure.

## Selection

Do not compare heterogeneous opportunities solely by raw percentage.

Selection may consider:

- net expected value;
- risk-adjusted return;
- execution certainty;
- capital required;
- latency sensitivity;
- existing exposure/correlation;
- strategy priority policy.

All routing policy must be configurable/versioned and testable.

## Conflict examples

- Do not open directional exposure while the same condition has unresolved one-leg structural exposure.
- Do not let DUAL40 and Directional Edge independently place conflicting orders on the same outcome.
- Do not allocate capital twice before the first plan is reconciled.

## Output

The router emits either:

- one approved candidate for risk/execution;
- a controlled compatible set when explicitly supported;
- or no action with a reason.

The router does not submit orders.
