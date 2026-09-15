# V3.1 Domain Contracts

## Purpose

The V3 domain is a pure, immutable boundary between data acquisition, decision logic,
risk, execution planning, reconciliation, and persistence. It imports no network,
database, or vendor SDK code and performs no side effects.

## Closed scope

- Assets: `BTC`, `ETH`, `SOL`, `XRP`
- Horizons: `5m`, `15m`, `1h`
- Outcomes: `UP`, `DOWN`
- Default application mode remains `PAPER`
- LIVE trading and automatic LIVE arming remain disabled

`Asset` and `Horizon` are the canonical scope definitions. Bootstrap string settings are
derived from these enums so scope is not duplicated.

## Enumerations

The domain defines closed enums for:

- `Asset`
- `Horizon`
- `OutcomeSide`
- `ReferenceKind`
- `StrategyKind`
- `DecisionAction`
- `TradingMode`
- `OrderSide`
- `TimeInForce`
- `OrderStatus`

`DecisionAction.ABSTAIN` is a normal strategy result. `OrderStatus.ACKNOWLEDGED`,
`PARTIALLY_FILLED`, and `FILLED` are distinct states.

## Contracts

### Market identity and data

- `MarketToken` maps a stable token ID to an explicit outcome.
- `Market` requires exactly one UP and one DOWN token, unique token IDs, a canonical
  asset/horizon, a UTC window, and settlement-source lineage.
- `OfficialReference` represents authoritative market-rule or settlement data.
- `ProxyReference` represents fast external predictive data.
- `BookLevel` and `OrderBookSnapshot` represent finite, immutable CLOB depth with source
  and receive timestamps.

Official and proxy references are separate concrete types with fixed, non-overridable
`ReferenceKind` values. Neither can be silently constructed as the other.

### Features and forecasts

- `FeatureValue` carries a finite value, source, and source event timestamp.
- `FeatureVector` requires unique feature names and rejects features from after its
  generation timestamp.
- `ProbabilityForecast` records model, calibration, and feature-schema versions and
  requires exact complementary binary probabilities.

### Strategy and risk

- `StrategyCandidate` records required capital, expected net value, quality, lifetime,
  and strategy identity.
- Directional candidates require an outcome side.
- Structural-arbitrage candidates cannot carry a directional outcome.
- `StrategyDecision` explicitly records `TRADE` or `ABSTAIN`; a trade requires a matching,
  unexpired candidate.
- `RiskDecision` fails closed: denial requires a reason and approves zero capital.

Strategy contracts contain no submit, execute, place-order, or cancellation method.

### Planning, reconciliation, and ledger

- `OrderIntent` is side-effect-free order intent data.
- `ExecutionPlan` holds immutable PAPER/future-LIVE plan data and unique client order IDs.
- `OrderState` preserves acknowledgement, partial fill, full fill, cancellation, expiry,
  rejection, and unknown-state semantics.
- `Fill`, `Position`, `Settlement`, and `LedgerEntry` retain explicit identifiers,
  timestamps, source/account lineage, and finite financial values.

The existence of a `TradingMode.LIVE` enum and plan value does not provide a LIVE gateway,
arming mechanism, credentials, signing, or order submission.

## Validation policy

- Decision snapshots are frozen, slot-based dataclasses.
- Collection fields that affect decisions must be tuples.
- Identifiers and source names must be non-empty and already trimmed.
- Persistable timestamps must be timezone-aware UTC.
- Financial and model numerics use finite `Decimal` values; NaN and infinity are rejected.
- Prediction-market prices and probabilities are bounded to `[0, 1]`.
- Missing or internally inconsistent critical state is rejected during construction.

## Ownership

The domain layer defines data contracts only. Future adapters normalize into these
contracts; strategies emit candidates/decisions; risk emits risk decisions; execution
consumes plans; reconciliation produces states/fills/positions; storage persists snapshots.
No domain type performs network or database I/O.
