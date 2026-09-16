# Phase Status

This file records phase promotion evidence. A phase is promoted only after its required
local and VPS gates pass.

| Phase | Commit SHA | Local result | VPS result | Tests | Unresolved issues | Timestamp (UTC) | Promotion |
|---|---|---|---|---|---|---|---|
| V3.0 | `00e83abbf2667bcb2d83caa8d1528ce2b4ff9e49` | Accepted | Accepted on Ubuntu 24.04 / Python 3.12.3 (user-provided baseline) | compileall, pytest, ruff, mypy, diff check | None reported | Before 2026-09-15 | Accepted |
| V3.1 | `8853fc26dc3c9c8c170317c77e2255cfaf0e8ac1` | Accepted: compileall; 68 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 68 pytest passed; ruff and mypy passed; diff check and status clean | 57 focused domain/security tests; 68 full-suite tests | None | 2026-09-15T14:30:55Z | Accepted |
| V3.2 | `51ae8a26836677528e1d2ce8855d658965799f9c` | Accepted: compileall; 104 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 104 pytest passed; ruff and mypy passed; diff check and status clean; public smoke passed | 35 focused market-data/integration/security tests; 104 full-suite tests | None | 2026-09-15T19:01:19Z | Accepted |
| V3.3 | `7cc3a6dbea1b766cddefe1186f37451dcb53d4e1` | Accepted: compileall; 109 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 109 pytest passed; ruff and mypy passed; diff check and status clean | 5 focused migration-boundary tests; 109 full-suite tests | None | 2026-09-15T19:08:54Z | Accepted |
| V3.4 | `8763cc3b8277482e96ef0476d06c7a7591291ec9` | Accepted: compileall; 147 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 147 pytest passed; ruff and mypy passed; diff check and status clean; all 12 live public markets validated | 65 focused identity/PTB/reference tests; 147 full-suite tests | None | 2026-09-15T19:20:36Z | Accepted |
| V3.5 | `af2c89032f0055e8161f8940537c732a68382783` | Accepted: compileall; 163 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 163 pytest passed; ruff and mypy passed; diff check and status clean; live read-only fee/book simulation passed | 16 focused pricing/regression tests; 163 full-suite tests | None | 2026-09-15T19:26:28Z | Accepted |
| V3.6 | `15391f1cc47a95f67d0f37ab1b728eabcd259adc` | Accepted: compileall; 179 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 179 pytest passed; ruff and mypy passed; diff check and status clean; live read-only paired scan passed | 16 focused structural/replay/regression tests; 179 full-suite tests | None | 2026-09-15T19:32:23Z | Accepted |
| V3.7 | `71f306cea638a2b30becc7cb672dd60d895b0f6c` | Accepted: compileall; 191 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 191 pytest passed; ruff and mypy passed; diff check and status clean; live-public fail-closed ABSTAIN passed | 13 focused directional/boundary tests; 191 full-suite tests | None | 2026-09-15T19:37:37Z | Accepted |
| V3.8 | `4c221b20de7deaa348ae83813ebed34acbb7d1dd` | Accepted: compileall; 201 pytest passed; ruff and mypy passed; diff check clean; all 12 buckets unpromoted | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 201 pytest passed; ruff and mypy passed; diff check and status clean; all 12 buckets unpromoted | 10 focused model/security tests; 201 full-suite tests | No historical corpus supplied; no model promoted | 2026-09-15T19:45:02Z | Accepted |
| V3.9 | `e3e6abf7a327458ca736980e70a9da86aa8e784f` | Accepted: compileall; 211 pytest passed; ruff and mypy passed; diff check clean; deterministic risk smoke passed | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 211 pytest passed; ruff and mypy passed; diff check and status clean; deterministic risk smoke passed | 10 focused risk/regression tests; 211 full-suite tests | None | 2026-09-15T19:50:09Z | Accepted |
| V3.10 | `901fd13e56c79fdc78cb7908bfd5b70359176507` | Accepted: compileall; 219 pytest passed; ruff and mypy passed; diff check clean; deterministic router smoke passed | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 219 pytest passed; ruff and mypy passed; diff check and status clean; deterministic router smoke passed | 8 focused router/regression tests; 219 full-suite tests | None | 2026-09-15T19:53:37Z | Accepted |
| V3.11 | `934486ed9672016fce77543d4699e070785692dc` | Accepted: compileall; 228 pytest passed; ruff and mypy passed; diff check clean; durable PAPER smoke passed | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 228 pytest passed; ruff and mypy passed; diff check and status clean; durable PAPER smoke passed | 9 focused execution/security tests; 228 full-suite tests | None | 2026-09-15T19:57:39Z | Accepted |
| V3.12 | `a11c6456e2e1dc1c21583499ae4e8b79936823f8` | Accepted: compileall; 230 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 230 pytest passed; ruff and mypy passed; diff check and status clean | 12 focused replay/reporting tests; 230 full-suite tests | No historical corpus; no strategy promoted | 2026-09-15T20:00:15Z | Accepted |
| V3.13 | `2344ec97308d493dce8d576bad47a05184fb79af` | Accepted: compileall; 242 pytest passed; ruff and mypy passed; diff check clean; dashboard smoke passed | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: dashboard service active; nginx config valid; health/live 200; readiness 503 fail-closed; focused 9 passed; full 239 passed; ruff and mypy passed; diff check and status clean | 12 focused observability/dashboard/security tests locally; 9 focused V3.13 tests on VPS; 242 local full-suite tests; 239 VPS full-suite tests | None | 2026-09-15T20:19:52Z | Accepted |
| V3.14 | `03ff70d78daa5117944b539b667b5439283e1d1b` | Accepted: compileall; 242 pytest passed; ruff and mypy passed; diff check clean; PRE-LIVE security focused tests passed | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: dashboard service remained active; health/live 200; readiness 503 fail-closed; watchdog confirmed PAPER/live=false/auto_arm=false/real_orders=false; focused 6 passed; full 242 passed; ruff and mypy passed; diff check and status clean | 6 focused PRE-LIVE/security tests; 242 full-suite tests | Hard stop remains before LIVE arming or any real order | 2026-09-15T20:22:13Z | Accepted |
| V3.15 | `17d88547729398e485c0989ed9d3fbc267f0878e` | Infrastructure accepted: compileall; 253 pytest passed; ruff and mypy passed; diff check clean; shadow report generated | Infrastructure accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: shadow timer active; dashboard service active; `/health/shadow-ready` 200; `/health/trading-ready` 503; focused 13 passed; full 253 passed; ruff and mypy passed; diff check and status clean; restart increased append-only shadow event count from 2 to 3 | 13 focused shadow/dashboard/security tests; 253 full-suite tests | Evidence accumulating; `AWS_ROOT_PROFILE_SECURITY_DEBT`; no final `v3.15.0` tag until burn-in/sample gates are evaluated | 2026-09-15T21:51:22Z | V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING |

## V3.1 VPS evidence

- Target revision tested: `b1db2d5a5cdefc24a6c5813afd79b1283dc40f2a`
- Correct focused paths: the four `tests/unit/test_domain_*.py` files plus
  `tests/security/test_domain_boundaries.py`
- Correct focused result: 57 passed
- Full result: 68 passed
- Python: 3.12.3
- AWS Systems Manager full acceptance command: `5d9bc0d4-5ad7-4530-b909-657116adac98`
- The earlier `tests/unit/domain` failure was a nonexistent-path invocation error, not a
  product or test failure. Tests were not moved or deleted.

## V3.2 VPS evidence

- Target revision tested: `51ae8a26836677528e1d2ce8855d658965799f9c`
- Python: 3.12.3
- Full result: 104 passed
- Ruff: passed
- mypy: no issues in 39 source files
- compileall and `git diff --check`: passed
- Final AWS Systems Manager acceptance command:
  `30531134-470f-42a1-9d50-c599e5070d8f`
- Read-only public-network samples from the accepted run:
  - Binance REST round trip: 259.88 ms; server clock skew sample: 123.51 ms
  - Binance BTC aggregate-trade source age: 123.96 ms
  - Polymarket CLOB REST round trip: 104.42 ms; server clock skew sample: 96.96 ms
  - Polymarket Gamma REST round trip: 45.56 ms
  - Chainlink RTDS BTC 30-second TWAP source age: 1635.28 ms; publisher age: 386.28 ms
- RTDS framing diagnostic command: `5eff41a1-dc6d-4866-9c8f-79710d7ba770`.
  It established that the live public feed can emit empty text frames and a subscription
  payload before updates. Empty/control frames are ignored, subscription payloads are
  not normalized as updates, and arbitrary non-JSON frames still fail closed.
- The earlier SSM failures were acceptance-harness issues (checkout ownership, cache
  directory, and unobserved RTDS framing), not product-test failures. No global Git
  safety setting was weakened and the project remains owned and operated by `ubuntu`.

## V3.3 VPS evidence

- Target revision tested: `7cc3a6dbea1b766cddefe1186f37451dcb53d4e1`
- Python: 3.12.3
- Focused migration-boundary result: 5 passed locally
- Full VPS result: 109 passed
- Ruff: passed
- mypy: no issues in 39 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `ccf7fab8-2937-45bb-b9b2-a7d59472339a`
- The ignored audit clones under `runtime/data/` were inspection-only and were neither
  committed nor deployed. No WhaleSignal runtime module or order path was migrated.

## V3.4 VPS evidence

- Target revision tested: `8763cc3b8277482e96ef0476d06c7a7591291ec9`
- Python: 3.12.3
- Focused canonical identity/PTB/reference result: 65 passed locally
- Full local and VPS result: 147 passed
- Ruff: passed
- mypy: no issues in 40 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `ce21feb1-0a00-410d-a723-929f9b278a93`
- Read-only Gamma validation accepted all 12 current asset/horizon markets. Request
  latency ranged from 15.76 ms to 44.53 ms in this sample.
- Live rules confirmed Chainlink 60-second TWAP authority for all 5m/15m buckets and
  Binance one-hour candle authority for all 1h buckets. No proxy substitution occurred.

## V3.5 VPS evidence

- Target revision tested: `af2c89032f0055e8161f8940537c732a68382783`
- Python: 3.12.3
- Focused pricing/regression result: 16 passed locally
- Full local and VPS result: 163 passed
- Ruff: passed
- mypy: no issues in 43 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `bb6805b5-9f18-4ccd-8d38-441e344e40c1`
- Read-only BTC 5m UP sample: 5 requested/filled shares; 0.43 VWAP and worst
  price; 0.08579 USDC dynamic taker fee; 0.4484459 buffered all-in cost per share.
  Book and fee-metadata request latency were 66.46 ms and 53.50 ms, respectively.

## V3.6 VPS evidence

- Target revision tested: `15391f1cc47a95f67d0f37ab1b728eabcd259adc`
- Python: 3.12.3
- Focused structural/replay/regression result: 16 passed locally
- Full local and VPS result: 179 passed
- Ruff: passed
- mypy: no issues in 46 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `83efda3d-bd80-4d1d-8e0e-e8aa71fe0950`
- The deterministic replay matrix passed at 10/25/50/100/200/500 ms with explicit
  both-leg, partial, one-leg, unwind, loss, and receive-time no-lookahead coverage.
- Read-only BTC 5m scan: UP/DOWN best asks 0.64/0.37; source skew 5 ms; both BUY+MERGE
  and SPLIT+SELL returned `NET_ECONOMICS_BELOW_MINIMUM`. No trade or order was emitted.

## V3.7 VPS evidence

- Target revision tested: `71f306cea638a2b30becc7cb672dd60d895b0f6c`
- Python: 3.12.3
- Focused Directional Edge/boundary result: 13 passed locally
- Full local and VPS result: 191 passed
- Ruff: passed
- mypy: no issues in 49 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `3e8b5aaf-68a4-4123-ae12-6fbeecd7d090`
- A current public BTC 5m market with no official PTB/model/calibration returned
  `ABSTAIN / OFFICIAL_PTB_UNAVAILABLE` and no candidate. No synthetic forecast was used.

## V3.8 VPS evidence

- Target revision tested: `4c221b20de7deaa348ae83813ebed34acbb7d1dd`
- Python: 3.12.3 from the existing project virtual environment
- Focused model/evaluation/security result: 10 passed locally and on the VPS
- Full local and VPS result: 201 passed
- Ruff: passed
- mypy: no issues in 54 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `9a151b89-f309-43c7-84a8-0847a59c72d4`
- The readiness probe confirmed exactly twelve `UNPROMOTED` asset/horizon buckets,
  each with zero samples and `ready=false`. No historical corpus was supplied, so no
  champion or calibration artifact was fabricated or promoted.
- The earlier command `85de81b0-d9af-40d2-aca5-9eadd68aa46c` used system `python3`,
  which lacks pytest; compileall passed before the invocation stopped. The accepted
  rerun used the repository's existing `.venv/bin/python` and is the canonical result.

## V3.9 VPS evidence

- Target revision tested: `e3e6abf7a327458ca736980e70a9da86aa8e784f`
- Python: 3.12.3 from the existing project virtual environment
- Focused unified-risk/regression result: 10 passed locally and on the VPS
- Full local and VPS result: 211 passed
- Ruff: passed
- mypy: no issues in 55 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `b954202f-6b92-464c-89af-9484af9cc25e`
- Deterministic smoke approved an all-in fixed 5 USDC candidate and denied the identical
  candidate when the global kill switch was active. No order or network path was used.

## V3.10 VPS evidence

- Target revision tested: `901fd13e56c79fdc78cb7908bfd5b70359176507`
- Python: 3.12.3 from the existing project virtual environment
- Focused router/regression result: 8 passed locally and on the VPS
- Full local and VPS result: 219 passed
- Ruff: passed
- mypy: no issues in 56 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `de69653b-f695-4c54-8ae0-feb7511b8005`
- Deterministic smoke proposed one revision-bound claim, rejected the same candidate
  after simulated restart, and reconciled the claim to the next revision. No order or
  network path was used.

## V3.11 VPS evidence

- Target revision tested: `934486ed9672016fce77543d4699e070785692dc`
- Python: 3.12.3 from the existing project virtual environment
- Focused PAPER execution/security result: 9 passed locally and on the VPS
- Full local and VPS result: 228 passed
- Ruff: passed
- mypy: no issues in 58 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `59e88946-dea2-4d1c-9c5d-d9ea4da936dc`
- Durable smoke recorded one partial fill and one audit event, then returned the exact
  result as a restart duplicate. LIVE remained interface-only and no network order path
  existed.

## V3.12 VPS evidence

- Target revision tested: `a11c6456e2e1dc1c21583499ae4e8b79936823f8`
- Python: 3.12.3; focused replay/reporting: 12 passed; full suite: 230 passed
- Ruff passed; mypy found no issues in 59 source files; compile/diff/status clean
- AWS Systems Manager acceptance command: `e6aff191-83a9-4774-9f94-e15ea1bbc55a`
- No historical corpus was supplied, so no model or strategy was promoted.

## V3.13 VPS evidence

- Target revision tested: `2344ec97308d493dce8d576bad47a05184fb79af`
- Python: 3.12.3 from the existing project virtual environment
- Service: `direction-engine-v3-dashboard.service` active under systemd, running as
  `/home/ubuntu/direction-engine-v3/.venv/bin/python -m direction_engine_v3.app.server`
- Nginx: `nginx -t` passed; dashboard proxy installed on localhost-only config
- Health: `/health/live` returned 200; `/health/ready` returned 503 with
  `trading_readiness=FAILING` because LIVE remains disabled and unarmed
- Smoke: dashboard status, watchdog status, and local dashboard smoke passed with
  `APP_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`, `LIVE_AUTO_ARM=false`, and
  `real_order_submission=false`
- Focused V3.13 result: 9 passed on the VPS
- Full VPS result: 239 passed
- Ruff: passed; mypy: no issues in 66 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `5e646f1e-d1a1-4b8e-b636-eeab6490cf00`
- Earlier V3.13 SSM failures were command-packaging and root/git ownership harness errors,
  not product failures. The accepted run used `ubuntu` for project Git/test operations and
  root only for authorized direction-engineV3 systemd/nginx deployment.

## V3.14 VPS evidence

- Target revision tested: `03ff70d78daa5117944b539b667b5439283e1d1b`
- Python: 3.12.3 from the existing project virtual environment
- Dashboard service remained active; `/health/live` returned 200 and `/health/ready`
  returned 503 fail-closed while LIVE remained disabled and unarmed
- Watchdog confirmed `APP_MODE=PAPER`, `LIVE_TRADING_ENABLED=false`,
  `LIVE_AUTO_ARM=false`, and `real_order_submission=false`
- Focused PRE-LIVE/security result: 6 passed on the VPS
- Full VPS result: 242 passed
- Ruff: passed; mypy: no issues in 67 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `e229b281-425f-4856-ac09-d655c49d89ad`
- V3.14 ended at the PRE-LIVE hard stop. LIVE was not enabled, LIVE was not armed,
  and no real Polymarket order or signing/submission path was implemented.

## V3.15 infrastructure VPS evidence

- Target revision tested: `17d88547729398e485c0989ed9d3fbc267f0878e`
- AWS caller identity: `arn:aws:iam::605618941421:root`
- AWS security debt recorded: `AWS_ROOT_PROFILE_SECURITY_DEBT`
- Python: 3.12.3 from the existing project virtual environment
- Services:
  - `direction-engine-v3-dashboard.service`: active
  - `direction-engine-v3-shadow.timer`: active and waiting; next refresh scheduled
  - `direction-engine-v3-shadow.service`: project-owned oneshot report refresh
- Health:
  - `/health/live`: 200
  - `/health/shadow-ready`: 200
  - `/health/trading-ready`: 503 while LIVE remains disabled/unarmed
- Reports:
  - `runtime/reports/shadow_summary.json`: generated
  - `runtime/reports/shadow_summary.md`: generated
  - status: `V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING`
  - buckets: 12, all currently `INSUFFICIENT_SAMPLE`
- Restart/recovery: restarting the shadow oneshot increased append-only shadow event
  count from 2 to 3 without enabling LIVE or exposing real order submission
- Focused V3.15 result: 13 passed on the VPS
- Full VPS result: 253 passed
- Ruff: passed; mypy: no issues in 72 source files
- compileall, `git diff --check`, and `git status --short`: passed/clean
- AWS Systems Manager acceptance command: `5716486b-c214-4301-894c-70ba7278d43c`
- Earlier V3.15 SSM command `c4d9c52c-ceb0-48fb-ab75-344529420322` passed code/tests
  but failed the new health endpoint check because the dashboard service had not been
  restarted after the fast-forward. The accepted rerun restarted only project services.
- No `v3.15.0` tag was created. V3.15 final acceptance remains blocked on the real
  burn-in and sample gates.

## V3.15.2 local evidence

- Status: `V3.15.2_CORPUS_ACCUMULATING` pending user-context VPS acceptance through
  `scripts/v3152_ssm_accept.ps1`.
- Directional runtime completion is PAPER/SHADOW-only. LIVE remains disabled and
  unarmed, and real Polymarket order submission remains absent.
- Official/proxy separation was tightened: the shadow daemon no longer constructs
  `OfficialReference` from `ProxyReference`; official references are owned by
  `market_data.official`.
- Durable PTB persistence, directional corpus records, per-bucket shadow model state,
  external temporal feature state, and read-only dashboard/API visibility were added.
- Model readiness remains evidence-gated. No bucket is promoted without validated
  walk-forward/corpus evidence; missing corpus reports `TRAINING_CORPUS_REQUIRED`.
- Local focused V3.15.2 tests: 14 passed.
- Local full suite: 268 passed.
- Local `python -m compileall src tests`: passed.
- Local Ruff: passed.
- Local mypy: no issues in 79 source files.
- Local `git diff --check`: passed.
- Bridge self-test: `powershell -NoProfile -ExecutionPolicy Bypass -File
  .\scripts\v3152_ssm_accept.ps1 -SelfTest` passed.
- Source scans found no new secret, LIVE enablement, real-order, proxy-to-official
  shortcut, scope-expansion, fake PTB/model/settlement, or Structural Arb weakening
  in the V3.15.2 implementation.
- VPS acceptance still requires the user-context SSM bridge because the Codex sandbox
  does not hold the Windows user's AWS profile/session.

## V3.15.3 local evidence

- Status before VPS boundary proof: `V3.15.3_PTB_RUNTIME_BLOCKED`.
- V3.15.3 adds persistent official-reference runtime ownership for Chainlink 60s TWAP
  and Binance 1h official candles without bulk-copying WhaleSignal architecture.
- The shadow daemon now carries typed official reference, persisted/restored PTB,
  feature status, executable-pricing status, PTB value, and PTB effective time in
  Directional evidence.
- Proxy data remains separately typed and cannot satisfy official reference or PTB.
- Historical model/corpus readiness remains unpromoted; no bucket is marked
  `SHADOW_CANDIDATE` by this phase.
- VPS acceptance must be performed through `scripts/v3153_ssm_accept.ps1` and must prove
  real boundary `PTB_READY` evidence before final status can become
  `V3.15.3_OFFICIAL_PTB_RUNTIME_ACTIVE`.
