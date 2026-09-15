# V3.12 Replay and Strategy Evaluation

Replay identity now freezes dataset, code commit, strategy/model versions, deterministic
seed, and a canonical configuration hash. Reports require all twelve asset/horizon
buckets independently and preserve theoretical versus executable opportunity counts.

The accepted structural replay already uses receive-time availability, governed
10/25/50/100/200/500 ms latency scenarios, depth, dynamic fees, slippage buffers,
partial fills, one-leg exposure, and unwind loss. Directional evaluation uses the V3.8
Brier, log loss, ECE, coverage, expectancy, drawdown, chronological cluster, and embargo
contracts. Each bucket must record explicit promotion/rejection evidence; positive
aggregate PnL alone cannot promote anything. No historical corpus was supplied, so no
strategy or model is promoted in V3.12.
