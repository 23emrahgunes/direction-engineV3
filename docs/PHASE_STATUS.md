# Phase Status

This file records phase promotion evidence. A phase is promoted only after its required
local and VPS gates pass.

| Phase | Commit SHA | Local result | VPS result | Tests | Unresolved issues | Timestamp (UTC) | Promotion |
|---|---|---|---|---|---|---|---|
| V3.0 | `00e83abbf2667bcb2d83caa8d1528ce2b4ff9e49` | Accepted | Accepted on Ubuntu 24.04 / Python 3.12.3 (user-provided baseline) | compileall, pytest, ruff, mypy, diff check | None reported | Before 2026-09-15 | Accepted |
| V3.1 | `8853fc26dc3c9c8c170317c77e2255cfaf0e8ac1` | Accepted: compileall; 68 pytest passed; ruff and mypy passed; diff check clean | Accepted on Ubuntu / Python 3.12.3 through AWS Systems Manager: compileall; 68 pytest passed; ruff and mypy passed; diff check and status clean | 57 focused domain/security tests; 68 full-suite tests | None | 2026-09-15T14:30:55Z | Accepted |

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
