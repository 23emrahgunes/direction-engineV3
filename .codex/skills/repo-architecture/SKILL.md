---
name: repo-architecture
description: Enforces direction-engineV3 module ownership, dependency direction, domain contracts, and strategy/execution separation. Use when creating modules, moving code, defining interfaces, or reviewing architecture.
---

# Repository Architecture

Maintain this flow:

DATA
→ NORMALIZATION/FRESHNESS
→ FEATURES
→ MODEL or STRUCTURAL SCANNER
→ STRATEGY
→ OPPORTUNITY ROUTER
→ RISK
→ EXECUTION PLAN
→ GATEWAY
→ RECONCILIATION
→ LEDGER

## Ownership rules

- `adapters/`: external protocols and transport.
- `market_data/`: normalized source state, freshness, lineage, book/reference caches.
- `features/`: deterministic feature calculations only.
- `models/`: probability estimation, calibration, artifacts, promotion.
- `strategies/`: pure candidate/decision logic; no order submission.
- `pricing/`: fees, VWAP, slippage, all-in cost, edge.
- `risk/`: liquidity/portfolio/exposure/sizing/kill switches.
- `execution/`: execution plans, gateways, state machine, reconciliation, unwind.
- `storage/`: persistence and repository interfaces.
- `replay/`: deterministic event-time replay.
- `evaluation/`: metrics and model/strategy evaluation.
- `observability/`: logs, metrics, health, audit.

## Dependency rules

Prefer dependencies toward stable domain contracts.

Forbidden patterns:

- strategy importing LIVE network client;
- model submitting/cancelling orders;
- dashboard mutating trading state except through explicit control interfaces;
- persistence deciding strategy;
- Polymarket price silently becoming the official settlement reference;
- structural arbitrage importing direction probability.

## Domain contracts

Represent at minimum:

- Market
- MarketToken / Outcome
- OrderBookSnapshot
- OfficialReference
- ProxyReference
- FeatureVector
- ProbabilityForecast
- StrategyCandidate
- RiskDecision
- ExecutionPlan
- OrderState
- Fill
- Position
- Settlement
- LedgerEntry

Use immutable dataclasses/value objects when practical for decision-time state.

## Shared kernel

Directional Edge, Structural Arbitrage, and optional DUAL40 may share:

- normalized market/book data;
- fee logic;
- execution simulation;
- risk;
- gateways;
- reconciliation;
- ledger;
- observability.

They must not share alpha/decision logic by accident.
