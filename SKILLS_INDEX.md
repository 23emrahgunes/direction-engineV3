# direction-engineV3 Skill Pack

This pack contains repo-specific Codex skills for the direction-engineV3 project.

## Included skills

| Skill | Primary purpose |
|---|---|
| `engineering-governance` | Bounded changes, evidence, anti-loop, completion gates |
| `repo-architecture` | Module ownership and dependency boundaries |
| `python-realtime-systems` | asyncio/WebSocket/reconnect/concurrency |
| `polymarket-platform` | Polymarket discovery/CLOB/auth/orders/fees/settlement |
| `market-data-engineering` | timestamps, freshness, PTB/reference lineage |
| `market-microstructure` | book/order-flow/microprice/liquidity features |
| `prediction-market-trading` | binary probability, EV, PTB/TTE, ABSTAIN |
| `directional-edge` | BTC/ETH/SOL/XRP × 5m/15m/1h probability-edge strategy |
| `structural-arbitrage` | model-free BUY+MERGE / SPLIT+SELL |
| `risk-management` | exposure, correlation, drawdown, kill switches |
| `execution-engineering` | order state, PAPER/LIVE gateways, reconciliation |
| `model-evaluation` | calibration, Brier, LogLoss, walk-forward, promotion |
| `backtesting-replay` | deterministic no-lookahead execution replay |
| `testing-qa` | unit/integration/regression/replay acceptance |
| `trading-security` | secrets, LIVE arming, operator/network safety |
| `observability-deployment` | logs, metrics, health, systemd/deployment |
| `whalesignal-migration` | controlled migration from WhaleSignal |
| `strategy-router` | capital/condition conflicts across strategies |

## Recommended skill combinations

### V3 repository/bootstrap work

```text
$engineering-governance $repo-architecture $testing-qa
```

### Market-data work

```text
$engineering-governance $python-realtime-systems $market-data-engineering $polymarket-platform
```

### Directional strategy work

```text
$prediction-market-trading $market-microstructure $directional-edge $model-evaluation $risk-management
```

### Structural arbitrage work

```text
$structural-arbitrage $execution-engineering $risk-management $backtesting-replay
```

### WhaleSignal migration

```text
$engineering-governance $whalesignal-migration $testing-qa
```

### LIVE execution/security work

```text
$trading-security $execution-engineering $risk-management $observability-deployment
```

## Important

Do not invoke every skill on every task. Use the minimum set that matches the work.

The skills intentionally separate:

- prediction from pricing;
- pricing from execution;
- Directional Edge from Structural Arbitrage;
- PAPER from LIVE gateways;
- official reference from proxy/reference data.
