# direction-engineV3 — Repository Operating Rules

This repository is a production-oriented Polymarket research, paper-trading, replay,
and eventually guarded-live trading system.

## Scope

Primary market scope:

- BTC
- ETH
- SOL
- XRP
- 5m
- 15m
- 1h

Do not expand to unrelated markets unless the user explicitly changes scope.

## Default safety state

- Default operating mode is PAPER/RESEARCH.
- LIVE trading is disabled by default.
- Never enable LIVE automatically.
- Never infer permission to submit real orders from a request to test, refactor, deploy,
  inspect, backtest, or run paper trading.
- Never log, print, commit, store in SQLite, or expose private keys/API secrets.
- Never bypass geographic, account, exchange, or platform restrictions.

## Canonical architecture

DATA
→ NORMALIZATION / FRESHNESS
→ FEATURES
→ MODEL / STRUCTURAL SCANNER
→ STRATEGY DECISION
→ OPPORTUNITY ROUTER
→ RISK
→ EXECUTION PLAN
→ PAPER OR LIVE GATEWAY
→ RECONCILIATION
→ LEDGER
→ EVALUATION / OBSERVABILITY

Keep these ownership boundaries explicit.

## Hard architecture rules

1. Strategy modules never submit network orders directly.
2. Models never perform execution side effects.
3. Risk checks happen after a strategy candidate exists and before any state-changing execution.
4. Paper and LIVE use the same strategy decision and execution-plan contracts; only the gateway differs.
5. Official settlement/reference data and fast proxy/reference data are different types and must never be silently substituted.
6. Directional fair-value alpha must not consume Polymarket price/book as predictive alpha by default. Polymarket prices are used for valuation/execution unless a separately documented experiment explicitly says otherwise.
7. Structural arbitrage is model-free. Never use a directional prediction to manufacture or justify structural parity.
8. DUAL40/recovery logic must not leak into Directional Edge.
9. Directional Edge must not use Martingale or loss-recovery sizing.
10. Order acknowledgement is not proof of fill.
11. Every state-changing order flow must reconcile final order/fill/position state.
12. Missing, stale, inconsistent, or unverifiable trading-critical data fails closed.
13. Never convert missing real data into zero, a fabricated default, or a mock value in production paths.
14. Do not hide a trading-critical failure behind a fallback.
15. Every trading decision must be auditable from persisted inputs, model/strategy version, risk result, execution plan, and final outcome.

## Change discipline

Before editing:

1. Inspect the current implementation and tests.
2. Identify the owning module.
3. Identify upstream/downstream contracts.
4. Identify regression risk.
5. Prefer the smallest coherent change.

While editing:

- Do not rewrite working modules merely to make them stylistically different.
- Do not mix unrelated refactors with behavior changes.
- Keep public contracts stable unless the task requires changing them.
- Do not create duplicate "v2/v3/final/fixed" files when the intended owner can be cleanly changed.
- Stop and diagnose if the same hypothesis/fix fails twice. Do not enter an unbounded patch loop.

Before claiming completion:

1. Run syntax/compile checks.
2. Run targeted unit tests.
3. Run affected integration tests.
4. Run relevant regression tests.
5. Run type/lint checks when configured.
6. Inspect `git diff`.
7. Report commands and results truthfully.
8. If something could not be run, say exactly what was not verified.

Never claim success without evidence.

## Migration policy

WhaleSignal is a source of proven logic and tests, not a folder to bulk-copy.

When migrating from WhaleSignal:

- inspect the source branch/file first;
- identify the latest relevant hardening changes;
- port behavior through V3 interfaces;
- preserve or adapt source tests;
- record source provenance in commit notes or migration docs;
- update deprecated API/client usage rather than copying it blindly;
- do not import unrelated historical hotfix debris.

## Data/time policy

Persist or carry separately where applicable:

- source event timestamp;
- local receive timestamp;
- normalized event timestamp;
- decision timestamp;
- order submit timestamp;
- exchange acknowledgement timestamp;
- reconciliation timestamp.

Use monotonic time for elapsed-duration measurements where appropriate.
Use UTC for persisted wall-clock timestamps.

## Definition of done

A task is done only when:

- behavior is implemented in the correct owner;
- tests covering the behavior pass;
- regressions relevant to the change pass;
- no trading-critical errors are suppressed;
- PAPER/LIVE safety defaults remain intact;
- resulting behavior is documented when the contract or strategy changed.
