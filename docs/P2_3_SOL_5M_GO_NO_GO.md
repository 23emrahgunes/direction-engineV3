# P2.3 SOL-5m Offline GO/NO-GO

> Local tooling preflight only. The authoritative P2.3 decision must be
> generated from the current VPS directional corpus DB via the accepted commit
> temporary worktree run.

## 1. Executive verdict

INSUFFICIENT_EVIDENCE — `P2_3_SOL_5M_INSUFFICIENT_EVIDENCE`

## 2. Dataset

- Code SHA: `0edd755ec3e0e97f81879ecd79e293e0c53fd99f`
- Dataset fingerprint: `467bf5c5b176b85f7321f420d83f67db7ff9cabfd9376ba00bb1f1ca120c0067`
- Feature schema: `v3.15.3-directional-official-ptb`
- Dataset schema: `directional_checkpoint_observations:v1`
- Unique conditions: 0
- Rows: 0
- UP / DOWN: 0 / 0
- Chronological span: None → None
- Checkpoint coverage: {120: 0, 90: 0, 60: 0, 45: 0}
- Excluded rows: {}
- Leakage violations: 0
- Label conflicts: 0

## 3. Frozen feature list

- Used: ptb_normalized_distance, tte_fraction, short_return, medium_return, momentum, realized_volatility, volatility_acceleration, trade_imbalance, external_book_imbalance, microprice_distance, flip_rate, regime_score
- Excluded placeholder: spot_perp_basis
- Excluded deterministic duplicate: signal_stability

## 4. Development walk-forward

| Fold | Train | Validation | Checkpoint | Regularization | Brier | LogLoss | ECE | After-Cost EV | Candidate Count |
|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|

## 5. Final challenger spec

```json
{}
```

## 6. Final holdout benchmark table

| Model | Conditions | Accuracy | Brier | LogLoss | ECE | Economic Coverage | After-Cost EV | Replay PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

## 7. Condition-level uncertainty

```json
null
```

## 8. Coefficients

```json
{}
```

## 9. GO gate table

| Gate | Result | Evidence |
|---|---|---|
| Evidence | FAIL | NO_SOL_5M_OFFICIAL_LABELED_CONDITIONS |

## 10. Failed gates

NO_SOL_5M_OFFICIAL_LABELED_CONDITIONS

## 11. Model governance / safety

- Artifact status: RESEARCH_ONLY
- execution_permission: NONE
- governance_rejection_reason: MODEL_NOT_PROMOTED
- PAPER execution: unchanged / not enabled
- LIVE execution: unchanged / not enabled
- final_holdout_evaluation_count: 0

## 12. Exact next action

Collect future SOL-5m conditions and checkpoint-time executable pricing evidence.
