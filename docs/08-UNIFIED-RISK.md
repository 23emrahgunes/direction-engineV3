# V3.9 Unified Risk Engine

The risk engine is a pure post-candidate, pre-execution boundary shared by all
strategies. It returns an immutable `RiskDecision` and exposes no network, order, or
storage operation.

## Portfolio gates

Every assessment projects the candidate's all-in required capital into maximum open
positions, total exposure, per-asset exposure, per-horizon exposure, and aggregate
correlated-crypto exposure. BTC, ETH, SOL, and XRP therefore share one cluster limit.
Concurrent windows for the same asset have an independent overlap limit.

New risk is denied for insufficient bankroll, stale or incomplete state, unreconciled
ledger state, daily loss, drawdown, active consecutive-loss cooldown, or the global kill
switch. Directional candidates use a fixed conservative maximum stake. V3.9 contains no
Kelly, Martingale, recovery ladder, or loss-dependent sizing.

Candidate `required_capital` is the strategy's projected all-in amount, including fees
and execution buffers. Risk never increases that amount and approves it exactly or
denies it; no silent partial resizing occurs.

## Liquidity gates

An assessment requires explicit evidence for source age, transport health, sequence
integrity, fee availability, spread, executable depth, projected price impact, quote
persistence, and observation count. Missing evidence fails closed. Low observation
count is distinct from persistent evidence of transient depth, and neither condition is
described as intentional market manipulation.

## Operational boundary

The kill switch must also be evaluated immediately before a future gateway submit;
V3.9's candidate-time decision does not replace that V3.11/V3.14 execution-time check.
All policies are explicit inputs. This phase sets no empirically asserted production
limits and leaves PAPER as the default with LIVE disabled.

## Migration provenance

The pure V3 core selectively rewrites portfolio behavior from WhaleSignal commit
`d19c24b2237fe0937f9baf4dbf1cbfed29e19d1f` and liquidity behavior from commit
`b6b507d8f37420a89c3863e730fd9e655363daba`. SQLite access, ambient clocks, floats,
settings coupling, first-failure-only results, and the legacy `SPOOFING_RISK` label were
not copied. V3 instead consumes immutable reconciled inputs, uses UTC/`Decimal`, reports
all observed reasons, and describes only transient-liquidity evidence without alleging
intent.
