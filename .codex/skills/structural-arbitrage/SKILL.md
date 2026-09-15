---
name: structural-arbitrage
description: Implements model-free complete-set Polymarket structural arbitrage, including BUY+MERGE, SPLIT+SELL, depth/fee economics, two-leg risk, replay, and unwind analysis. Use for P3-style arbitrage work.
---

# Structural Arbitrage

Structural arbitrage is separate from directional alpha.

Never use `p_up`, momentum, or a directional model to declare complete-set parity.

## BUY + MERGE

For equal share quantity `q`:

`net_profit = q - buy_cost_up(q) - buy_cost_down(q) - fees - execution_buffer`

Costs must use full-depth executable VWAP for exact equal shares.

## SPLIT + SELL

For equal share quantity `q`:

`net_profit = sell_proceeds_up(q) + sell_proceeds_down(q) - fees - q - execution_buffer`

Again use executable depth.

## Quantity optimization

Optimize only across feasible equal-share quantities supported by both sides and configured caps.
Do not compare unequal outcome shares as a risk-free complete set.

## Required validation

Before reporting an opportunity require:

- matching condition/outcome pair;
- synchronized/fresh books;
- valid token mapping;
- available fee schedule;
- sufficient depth on both legs;
- positive net profit after explicit execution buffer;
- minimum ROI/profit gates.

## Two-leg execution risk

Two order submissions are not atomic unless the platform explicitly provides an atomic primitive.

Research and replay must measure:

- both-leg completion;
- one-leg completion;
- partial fill;
- time between legs;
- immediate unwind feasibility;
- unwind loss;
- final residual exposure.

## LIVE policy

A structural opportunity is not permission to trade LIVE.
LIVE requires the common execution/risk/security gates and explicit arming.

## WhaleSignal migration

Preserve the proven behavior of WhaleSignal P3 complete-set math and replay when migrating, but
adapt it to V3 domain contracts and current platform fee/API semantics.
