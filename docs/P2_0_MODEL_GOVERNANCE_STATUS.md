# P2.0 Model Governance Status

Program objective: prevent unproven Directional models from consuming PAPER capital,
build trustworthy supervised evaluation, and allow limited PAPER only after explicit
Asset × Horizon model governance approval.

Start SHA: `254dea0`

| Phase | Status | Commit | Deploy | Acceptance |
|---|---|---|---|---|
| P2.0.0 Directional PAPER → SHADOW_ONLY | PASSED_LOCAL | pending | required | `P2_0_0_SHADOW_ONLY_ACCEPTED` locally |
| P2.0.1 Fixed-checkpoint supervised dataset | NOT_STARTED | — | TBD | — |
| P2.0.2 Champion/challenger registry | NOT_STARTED | — | TBD | — |
| P2.0.3 Real feature-trained challengers | NOT_STARTED | — | TBD | — |
| P2.0.4 Chronological walk-forward | NOT_STARTED | — | TBD | — |
| P2.0.5 Calibration + after-cost EV | NOT_STARTED | — | TBD | — |
| P2.0.6 Promotion governance | NOT_STARTED | — | TBD | — |
| P2.0.7 Limited PAPER canary | NOT_STARTED | — | conditional | — |

## P2.0.0 Evidence

- New Directional PAPER execution permission defaults to `NONE`.
- Otherwise valid Directional `TRADE` assessments are preserved as strategy evidence but
  new PAPER execution is denied with `MODEL_NOT_PROMOTED`.
- `PAPER_RESEARCH_BASELINE` is treated as `HISTORICAL_RESEARCH_ONLY`.
- `PAPER_LOGISTIC:*` is treated as `SUSPECT`.
- Shadow evaluation, audit payloads, corpus placeholder flow, settlement scanning, and
  existing positions remain intact.
- LIVE remains disabled; `real_order_submission=false`.

## Latest Local Validation

- `python -m compileall src tests scripts`
  - Result: passed
- `python -m pytest tests/unit/test_shadow_daemon.py tests/security/test_model_governance.py tests/integration/test_dashboard_api.py -q`
  - Result: `41 passed`
- `python -m pytest -q`
  - Result: `436 passed`
- `python -m ruff check .`
  - Result: passed
- `python -m mypy src`
  - Result: `Success: no issues found in 93 source files`
- `git diff --check`
  - Result: passed
- `bash -n deploy/aws/paper_deploy.sh`
  - Result: Windows local `E_ACCESSDENIED`; expected to be validated by GitHub Ubuntu CI

## Open Items

- Deploy exact SHA after CI for P2.0.0 runtime behavior.
- Implement P2.0.1 fixed-checkpoint dataset capture next.
