# V3.8 Model Evaluation and Calibration

## Twelve independent buckets

Directional probability artifacts are isolated by the Cartesian product of BTC, ETH,
SOL, XRP and 5m, 15m, 1h. The registry requires exactly these twelve buckets and never
falls back across assets or horizons. Every bucket starts `ready=false`; no historical
training corpus was supplied in V3.8, so this phase promotes no model.

## Baseline and artifact contract

The baseline is an interpretable logistic model represented by an immutable,
dependency-free artifact. It freezes model version, feature-set version, ordered feature
names, coefficients, intercept, and a SHA-256 schema digest. Inference requires an exact
feature match. Predictive schemas reject Polymarket/CLOB/contract-price fields so venue
price remains valuation data rather than default alpha.

Reliability calibration is fitted only from labeled observations at or before its UTC
training cutoff. Fixed bins require a configured minimum sample count, cover the full
probability interval, and are frozen with an explicit calibration version. Forecasts
must occur after the cutoff and retain model, calibration, feature schema, and as-of
times.

## Evaluation and promotion

Evaluation is per asset/horizon bucket and reports Brier score, log loss, reliability
error (ECE), directional accuracy as a secondary metric, strategy coverage, expectancy
after costs, and maximum drawdown. Walk-forward splits are chronological, keep related
market clusters whole, apply an explicit embargo, and prohibit pooled buckets.

A challenger is promotable only when it has enough evaluation samples, strictly improves
both Brier and log loss over the champion, remains within ECE and drawdown limits, meets
coverage, and has positive after-cost expectancy. Passing code-level tests is not model
promotion evidence. Promotion requires a real historical corpus and recorded per-bucket
walk-forward results.

## Safety state

The accepted V3.8 state contains no trained coefficients, fabricated observations,
claimed metrics, or promoted champions. All twelve buckets remain explicitly
`UNPROMOTED`, making Directional Edge fail closed at its calibrated-model readiness gate.
Model modules perform no network or order I/O and do not load executable pickle/joblib
artifacts.
