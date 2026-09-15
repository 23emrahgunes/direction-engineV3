---
name: market-microstructure
description: Designs short-horizon order-book and trade-flow features for crypto/prediction-market research. Use for imbalance, microprice, spread/depth, aggressor flow, liquidity persistence, and regime features.
---

# Market Microstructure

Use microstructure features as evidence, not as magical indicators.

## Core features

Consider, with explicit window definitions:

- best bid/ask and spread;
- depth by distance from top of book;
- order-book imbalance;
- microprice;
- weighted mid;
- trade aggressor imbalance;
- signed volume;
- trade velocity;
- short-horizon returns;
- realized volatility;
- volatility acceleration;
- quote/depth persistence;
- book flicker;
- cancel-to-add behavior when observable;
- basis/spot-perp divergence.

## Windows

Every feature must define:

- source;
- lookback window;
- timestamp convention;
- missing-data behavior;
- minimum observations.

Do not mix 1s/5s/30s semantics under the same feature name.

## Leakage

Features at decision time may only use events observable at or before that decision timestamp.

## Liquidity-risk terminology

Public order-book patterns can indicate transient/ghost-liquidity risk.
Do not label activity as confirmed manipulation/spoofing without evidence of intent.

## Robustness

Prefer normalized values:

- bps/log returns;
- z-scores relative to recent volatility;
- depth ratios;
- quantities scaled by executable stake.

Raw dollar moves are not comparable across assets/regimes.

## Role in Directional Edge

Microstructure is one feature family. It should not override a missing official PTB/reference,
invalid settlement mapping, or a failed risk/execution gate.
