# P2.0 Model Governance Status

Program objective: prevent unproven Directional models from consuming PAPER capital,
build trustworthy supervised evaluation, and allow limited PAPER only after explicit
Asset × Horizon model governance approval.

Start SHA: `254dea0`

| Phase | Status | Commit | Deploy | Acceptance |
|---|---|---|---|---|
| P2.0.0 Directional PAPER → SHADOW_ONLY | ACCEPTED_RUNTIME | `3ac4b913568ba94e45e798448f2bdb0accb7ad5c` | Deploy PAPER #60 success | `P2_0_0_SHADOW_ONLY_ACCEPTED` |
| P2.0.1 Fixed-checkpoint supervised dataset | ACCEPTED_RUNTIME | `03f7019` | Deploy PAPER #61 success | checkpoint capture live; no historical backfill |
| P2.0.2 Champion/challenger registry | ACCEPTED_RUNTIME | `03f7019` | Deploy PAPER #61 success | bucket exact, fail-closed permissions |
| P2.0.3 Real feature-trained challengers | ACCEPTED_RUNTIME | `03f7019` | Deploy PAPER #61 success | dependency-free L2 logistic challenger; degenerate rejection |
| P2.0.4 Chronological walk-forward | PASSED_LOCAL | existing + tests | no runtime deploy alone | condition-isolated framework retained |
| P2.0.5 Calibration + after-cost EV | PASSED_LOCAL | existing + tests | no runtime deploy alone | calibration/economic gates retained |
| P2.0.6 Promotion governance | ACCEPTED_RUNTIME | `03f7019` | Deploy PAPER #61 success | no real promotion without gates |
| P2.0.7 Limited PAPER canary | NO_QUALIFIED_MODEL | `03f7019` | conditional | `P2_0_7_NO_QUALIFIED_MODEL` |

## P2.0.0 Evidence

- New Directional PAPER execution permission defaults to `NONE`.
- Otherwise valid Directional `TRADE` assessments are preserved as strategy evidence but
  new PAPER execution is denied with `MODEL_NOT_PROMOTED`.
- `PAPER_RESEARCH_BASELINE` is treated as `HISTORICAL_RESEARCH_ONLY`.
- `PAPER_LOGISTIC:*` is treated as `SUSPECT`.
- Shadow evaluation, audit payloads, corpus placeholder flow, settlement scanning, and
  existing positions remain intact.
- LIVE remains disabled; `real_order_submission=false`.
- GitHub CI #61 for `3ac4b91` completed successfully.
- GitHub Deploy PAPER #60 for `3ac4b91` completed successfully.
- Runtime API evidence through `127.0.0.1:8131`:
  - `/api/model/governance`: 12/12 buckets `execution_permission=NONE`,
    `governance_rejection_reason=MODEL_NOT_PROMOTED`.
  - `/api/dashboard`: `APP_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`,
    `LIVE_AUTO_ARM=false`, `real_order_submission=false`.
  - `/api/shadow/status`: `REAL_SHADOW_CYCLE`, `STRATEGY_EVALUATION`,
    `PTB_BOUNDARY_SCHEDULER`, and `PAPER_SETTLEMENT_SCAN` counters advanced.
  - Directional open count stayed `0`; latest Directional trade was
    `2026-10-03T08:33:09.786482+00:00`, before P2.0.0 deployment.
  - Live abstain records prove otherwise executable candidates were converted to
    `MODEL_NOT_PROMOTED` for both `PAPER_RESEARCH_BASELINE` and `PAPER_LOGISTIC:*`.

## P2.0.1–P2.0.7 Evidence

- `directional_checkpoint_observations` is added beside existing `directional_corpus`.
- Checkpoints are captured only for real observations within `120/90/60/45s ±10s`.
- Checkpoint uniqueness is enforced by condition, bucket, target TTE, and feature schema.
- Official outcome attachment updates checkpoint rows without accepting proxy labels.
- Dataset quality report includes per-target counts, labeled counts, duplicate rejects,
  missing feature counts, and future timestamp violations.
- PAPER model loading no longer fabricates intercept-only models as trade-worthy artifacts.
- L2 logistic challenger training is dependency-free and rejects single-class or
  near-zero-variance datasets as `DEGENERATE_MODEL_*`.
- Promotion permission remains exact to `(bucket, model_version)` and defaults to `NONE`.
- No real model is promoted from the current data; Directional remains SHADOW_ONLY.
- GitHub CI #62 for `03f7019` completed successfully.
- GitHub Deploy PAPER #61 for `03f7019` completed successfully; the deploy script
  validates exact checkout by comparing VPS `git rev-parse HEAD` to the expected SHA.
- Runtime dataset quality after deploy reports live checkpoint rows for 5m buckets,
  duplicate rejects `0`, missing feature counts `{}`, and future timestamp violations `0`.

## Latest Local Validation

- `python -m compileall src tests scripts`
  - Result: passed
- `python -m pytest tests/security/test_model_governance.py tests/unit/test_v3154_directional_paper_runtime.py -q`
  - Result: `14 passed`
- `python -m pytest tests/unit/test_shadow_daemon.py -q`
  - Result: `21 passed`
- `python -m pytest tests/integration/test_dashboard_api.py tests/integration/test_paper_dashboard_api.py -q`
  - Result: `21 passed`
- `python -m pytest -q`
  - Result: `440 passed`
- `python -m ruff check .`
  - Result: passed
- `python -m mypy src`
  - Result: `Success: no issues found in 93 source files`
- `git diff --check`
  - Result: passed
- `bash -n deploy/aws/paper_deploy.sh`
  - Result: Windows local `E_ACCESSDENIED`; expected to be validated by GitHub Ubuntu CI

## Open Items

- Collect real checkpoint observations; do not promote until real data satisfies gates.
