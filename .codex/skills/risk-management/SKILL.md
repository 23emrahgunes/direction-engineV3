---
name: risk-management
description: Defines fail-closed portfolio, liquidity, correlation, exposure, drawdown, sizing, and kill-switch controls for all direction-engineV3 strategies. Use before any PAPER/LIVE execution behavior is changed.
---

# Risk Management

Risk is shared infrastructure. Strategies propose; risk decides whether execution is allowed.

## Portfolio gates

Support configurable:

- maximum open positions;
- maximum total open exposure;
- per-asset exposure;
- per-horizon exposure;
- crypto-cluster exposure;
- maximum overlapping positions per asset;
- daily realized loss limit;
- maximum drawdown;
- consecutive-loss cooldown;
- global kill switch.

All calculations must include projected fees when they consume bankroll/exposure.

## Liquidity gates

Before execution check:

- book present;
- book fresh;
- no unresolved sequence gap;
- valid spread;
- sufficient executable depth;
- acceptable projected price impact;
- depth/quote persistence when required;
- transient-liquidity risk;
- fee schedule available when needed.

## Correlation

BTC/ETH/SOL/XRP positions are not independent.
Track aggregate crypto-direction/cluster exposure.

## Sizing

Directional default for initial research/paper:

- fixed conservative stake or explicitly configured fixed risk.

Only evaluate fractional Kelly after:

- probabilities are calibrated;
- sufficient out-of-sample sample size exists;
- drawdown and tail behavior are known.

Never implement Martingale or recovery ladders for Directional Edge.

## Fail closed

If bankroll, fee, exposure, position state, or ledger state is ambiguous, deny new execution until reconciled.

## Kill switch

A kill switch must block new state-changing orders and be checked immediately before submit,
not only when a candidate is generated.
