---
name: model-evaluation
description: Evaluates and promotes directional probability models using walk-forward splits, calibration, Brier/LogLoss, leakage checks, bucket metrics, and champion/challenger governance.
---

# Model Evaluation

Trading probability models must be evaluated as probability estimators, not only as classifiers.

## Core metrics

Track at minimum:

- Brier score;
- LogLoss;
- calibration curve/reliability;
- ECE or equivalent calibration error;
- accuracy/win rate as secondary context;
- coverage/trade rate after gates;
- expectancy/PnL after executable costs;
- max drawdown in paper/replay.

## Time-aware validation

Use chronological train/validation/test or walk-forward splits.

Never random-shuffle observations if it can leak later market regimes into earlier decisions.

Group/split carefully so multiple snapshots from the same market do not appear on both sides of an evaluation boundary when that would leak outcome information.

## Calibration

Calibration artifact must be:

- trained only on prior data;
- versioned;
- tied to model feature schema;
- tied to asset/horizon or validated pooling rule;
- frozen during inference.

## Buckets

Report BTC/ETH/SOL/XRP × 5m/15m/1h separately even if a shared model is used.

## Champion/challenger

A challenger cannot replace the champion merely because in-sample PnL is higher.

Promotion requires configured minimums for:

- sample size;
- class balance;
- calibration quality;
- Brier/LogLoss;
- out-of-sample expectancy;
- drawdown/risk;
- stability across windows.

Persist promotion evidence and artifact hashes.

## Leakage review

Before accepting a new feature ask:

- was the value knowable at decision time?
- does it contain outcome/settlement information from the future?
- is it derived from a table updated after resolution?
- does preprocessing fit on future samples?

Reject leakage even if metrics improve.
