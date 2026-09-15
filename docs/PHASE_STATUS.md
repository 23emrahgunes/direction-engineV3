# Phase Status

This file records phase promotion evidence. A phase is promoted only after its required
local and VPS gates pass.

| Phase | Commit SHA | Local result | VPS result | Tests | Unresolved issues | Timestamp (UTC) | Promotion |
|---|---|---|---|---|---|---|---|
| V3.0 | `00e83abbf2667bcb2d83caa8d1528ce2b4ff9e49` | Accepted | Accepted on Ubuntu 24.04 / Python 3.12.3 (user-provided baseline) | compileall, pytest, ruff, mypy, diff check | None reported | Before 2026-09-15 | Accepted |
| V3.1 | `8853fc26dc3c9c8c170317c77e2255cfaf0e8ac1` | Accepted: compileall; 68 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 68 pytest passed; ruff and mypy passed; diff check and status clean | 57 focused domain/security tests; 68 full-suite tests | None | 2026-09-15T14:30:55Z | Accepted |
| V3.2 | `51ae8a26836677528e1d2ce8855d658965799f9c` | Accepted: compileall; 104 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 104 pytest passed; ruff and mypy passed; diff check and status clean; public smoke passed | 35 focused market-data/integration/security tests; 104 full-suite tests | None | 2026-09-15T19:01:19Z | Accepted |

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
