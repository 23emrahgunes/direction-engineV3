# V3.7 Directional Edge

## Separation of concerns

Directional Edge converts external/official information into a pre-risk opportunity.
Polymarket data is used only for executable valuation. The external feature builder has
no CLOB/book/contract-price parameter, while the strategy accepts the already simulated
selected-side CLOB cost only after a calibrated probability exists.

The design selectively rewrites the external-alpha separation from WhaleSignal
Directional V2 (`c128731b3dfb84cbdd6c283e621553f1e534574f`). Its 5m constants, missing-to-zero
feature behavior, broad exception fallbacks, and any recovery sizing were rejected.

## Feature schema

The required, complete snapshot includes:

- official PTB normalized distance and canonical TTE fraction;
- short and medium external returns and momentum;
- realized volatility and volatility acceleration;
- Binance spot/perpetual basis;
- external order-book imbalance, microprice distance, and trade imbalance;
- signal stability, flip rate, and regime score.

Every field is a finite `Decimal`; missing inputs cannot become zero. The reference,
market, asset, feature source time, and feature schema version are retained. A proxy may
be a typed predictor, but it cannot establish the official PTB.

## Decision gates

`assess_directional_edge` returns explicit ABSTAIN reasons unless all strategy-owned
gates pass: active market, official persisted PTB, complete features, calibrated forecast,
matching 12-bucket calibration readiness/version, fresh forecast, signal stability,
bounded flip rate, unique side, fully executable identity-matched book cost, dynamic
fees/slippage/buffers, uncertainty buffer, and minimum net edge.

All thresholds are required policy inputs. V3.7 defines no empirical final defaults.
When gates pass, the output is a `StrategyCandidate`, not an order: common portfolio risk
still owns approval in V3.9, the router owns coordination in V3.10, and execution planning
is later. Conservative requested size comes from the supplied pricing simulation; there
is no adaptive loss sizing.

## Model status

V3.7 consumes a versioned `ProbabilityForecast` and `CalibrationReadiness`; it does not
invent one. The twelve-bucket baseline, chronological calibration, and promotion evidence
are V3.8. Until those artifacts exist and are marked ready, live/public validation must
ABSTAIN with no candidate.

## Acceptance

Tests cover external feature completeness, PTB/TTE, no CLOB alpha, reference identity,
calibration/model absence, stale forecasts, stability/flip gates, side ties, executable
identity/depth, net edge, and no order/recovery methods. The VPS probe reads a current
public market identity and verifies that absent PTB/model/calibration produces
`OFFICIAL_PTB_UNAVAILABLE` and no candidate. It performs no market mutation.
