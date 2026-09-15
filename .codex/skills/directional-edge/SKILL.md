---
name: directional-edge
description: Implements the direction-engineV3 PTB-calibrated directional probability edge strategy for BTC/ETH/SOL/XRP 5m/15m/1h. Use for directional features, probability models, gates, pricing, entry windows, and ABSTAIN logic.
---

# Directional Edge V3

Primary scope:

BTC, ETH, SOL, XRP × 5m, 15m, 1h.

## Principle

Predict fair settlement probability from information independent of the Polymarket contract price,
then compare that probability with executable Polymarket cost.

Default separation:

EXTERNAL/OFFICIAL DATA
→ probability/fair value

POLYMARKET BOOK
→ executable price/cost

Do not use Polymarket price as predictive alpha in the default fair-value model.

## Decision pipeline

1. Validate market identity and settlement rule/source.
2. Validate canonical TTE.
3. Require valid/fresh official PTB/current reference when the market semantics require it.
4. Build external/proxy features.
5. Produce raw `p_up`.
6. Apply the approved calibration artifact.
7. Derive candidate side or ABSTAIN.
8. Require directional consistency with PTB-distance/regime gates as configured.
9. Simulate executable depth, fee, slippage, impact.
10. Calculate net edge and uncertainty margin.
11. Apply liquidity and portfolio risk gates.
12. Emit an `ExecutionPlan` or ABSTAIN.

## Feature families

Candidate features include:

- normalized PTB distance;
- TTE;
- realized volatility;
- short-horizon momentum/returns;
- volatility acceleration;
- spot/perp relationship;
- order-flow imbalance;
- book imbalance/microprice from external crypto venues;
- signal stability/flip rate;
- regime state.

Feature inclusion must be validated by walk-forward evaluation.

## Model buckets

Maintain independent evaluation/calibration buckets for:

- asset;
- horizon.

Do not assume BTC-5m calibration transfers to SOL-1h.

Shared models are allowed only if evaluation demonstrates improvement and calibration remains valid.

## Thresholds

Entry windows, minimum net edge, probability cutoffs, stability requirements, and uncertainty
buffers belong in versioned configuration/artifacts, not scattered magic constants.

Start from paper/research values and promote only with evidence.

## Risk

- No Martingale.
- No "recover the previous loss" stake escalation.
- Position sizing is independent of previous trade outcome except through portfolio risk/equity policy.
- Correlated simultaneous crypto positions count toward cluster exposure.

## Output

A directional strategy must return an auditable decision containing:

- market/condition;
- side;
- calibrated probability;
- model/artifact version;
- PTB/TTE state;
- key feature snapshot or feature hash;
- executable price estimate;
- fee/slippage;
- net edge;
- gate results;
- reason for TRADE or ABSTAIN.
