# direction-engineV3

`direction-engineV3` is a Python 3.12 research and paper-trading system for BTC, ETH,
SOL, and XRP markets with 5m, 15m, and 1h horizons.

V3.4 contains the repository skeleton, fail-safe bootstrap configuration, immutable
domain contracts, credential-free public market data, an audited WhaleSignal migration
map, and the canonical twelve-bucket market/PTB/reference engine. It does not yet contain
Directional Edge, Structural Arbitrage, or real order submission.

## Safety defaults

- `APP_MODE=PAPER`
- `LIVE_TRADING_ENABLED=false`
- `LIVE_AUTO_ARM=false`
- Package imports perform no network I/O and require no credentials.

## Architecture ownership

```text
DATA
→ NORMALIZATION / FRESHNESS
→ FEATURES
→ MODEL / STRUCTURAL SCANNER
→ STRATEGY
→ OPPORTUNITY ROUTER
→ RISK
→ EXECUTION PLAN
→ PAPER / LIVE GATEWAY
→ RECONCILIATION
→ LEDGER
→ EVALUATION / OBSERVABILITY
```

External protocols belong under `adapters`. Canonical timestamps, source health,
freshness, sequence integrity, retry, and bounded buffers belong under `market_data`.
Strategy namespaces contain decision logic only. Execution plans, gateways, and
reconciliation belong under `execution`; ledger persistence belongs under `storage`.

The V3.2 adapters expose only allowlisted public GET/WebSocket transport. They do not
contain authentication, mutation endpoints, order submission, or import-time I/O.

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall src tests
pytest
ruff check .
mypy src
```
