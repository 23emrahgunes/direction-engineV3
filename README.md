# direction-engineV3

`direction-engineV3` is a Python 3.12 research and paper-trading system for BTC, ETH,
SOL, and XRP markets with 5m, 15m, and 1h horizons.

V3.0 contains only the repository skeleton and fail-safe bootstrap configuration. It
does not contain Directional Edge, Structural Arbitrage, WhaleSignal migrations,
market-data implementations, or real order submission.

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

External protocols belong under `adapters`. Strategy namespaces contain decision logic
only. Execution plans, gateways, and reconciliation belong under `execution`; ledger
persistence belongs under `storage`. V3.0 provides package boundaries only.

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall src tests
pytest
ruff check .
mypy src
```
