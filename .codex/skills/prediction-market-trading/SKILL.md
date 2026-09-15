---
name: prediction-market-trading
description: Applies binary prediction-market probability, payoff, PTB, expected-value, pricing, and abstention concepts. Use when designing or reviewing trade decision math.
---

# Prediction Market Trading

A binary contract price is not merely "direction"; treat it as a tradable probability-like price
subject to fees, spread, liquidity, and market microstructure.

## Probability and value

Directional model output must be a probability:

- `p_up`
- `p_down = 1 - p_up` when the market is truly binary and exhaustive.

Do not convert a probability into a trade without comparing it to executable market economics.

## Executable edge

For a BUY candidate evaluate using executable/all-in cost, not website display price:

`net_edge ≈ calibrated_probability - all_in_cost_per_share - uncertainty_buffer`

The exact expected-value implementation should be explicit and tested.

Include:

- depth-aware VWAP;
- fee;
- slippage/impact;
- latency/execution buffer;
- model uncertainty margin.

## PTB/TTE

Distance to Price-to-Beat must be interpreted jointly with:

- time-to-expiry;
- volatility;
- asset;
- horizon.

Normalize distance rather than relying only on raw dollars.

## Confidence vs edge

High model confidence does not imply a good trade if the contract is overpriced.
A lower-confidence forecast can be attractive if price is sufficiently favorable and calibrated.

## Abstain

ABSTAIN is a first-class outcome.

Abstain on:

- insufficient edge;
- conflicting/unstable signals;
- stale/missing critical data;
- invalid PTB/reference;
- poor liquidity;
- failed risk;
- execution economics below threshold.

Do not force a trade in every market.
