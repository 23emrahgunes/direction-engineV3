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
