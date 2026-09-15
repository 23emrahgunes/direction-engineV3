---
name: backtesting-replay
description: Builds deterministic event-time market/order-book replay with latency, fees, slippage, partial fills, one-leg exposure, and no-lookahead guarantees. Use for historical validation and execution research.
---

# Backtesting and Replay

Backtest what could actually have been known and executed.

## Event time

Replay in decision/event timestamp order.

At replay time `t`, code may only observe state whose source/receive semantics make it available at or before `t`.

Never fetch a later book to make a historical decision better.

## Latency

Model configurable delays separately:

- data receive delay;
- strategy computation delay;
- order submit delay;
- exchange acknowledgement/fill delay.

Use multiple scenarios rather than one optimistic constant.

## Execution simulation

Use historical depth to model:

- exact requested quantity/stake;
- VWAP;
- fees;
- price impact;
- partial fill;
- FOK/FAK semantics as accurately as available data permits;
- maker fill uncertainty where maker simulation is used.

Do not count displayed depth as guaranteed fill.

## Structural arbitrage

Replay both legs with delay and record:

- both-leg success;
- one-leg exposure;
- unwind path;
- cycle PnL.

## Directional strategy

Record:

- raw/calibrated probability;
- executable price;
- net edge;
- gate reasons;
- outcome;
- PnL.

## Determinism

A replay should be reproducible from:

- dataset/version;
- config/version;
- model artifacts;
- code commit;
- deterministic seed where randomness exists.

## Reporting

Separate:

- gross theoretical edge;
- simulated executable edge;
- realized/simulated PnL.

Do not present theoretical opportunity count as executable profit.
