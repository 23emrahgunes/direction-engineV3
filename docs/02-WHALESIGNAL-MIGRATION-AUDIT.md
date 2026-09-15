# V3.3 WhaleSignal Migration Audit

WhaleSignal is evidence and provenance, not a V3 dependency. This audit inspected the
public repository and divergent Direction Engine branches without copying its tree into
V3. The ignored inspection clones under `runtime/data/` are not tracked artifacts.

Repository: <https://github.com/23emrahgunes/WhaleSignal>

## Audit method and branch selection

The audit enumerated all remote heads, compared branch ancestry, inspected source and
tests at named revisions, and preferred the narrow commit that established an invariant
over a later branch name that merely happened to contain it.

| Source line | Head inspected | Finding |
|---|---|---|
| P2.6 full | `04c680c008b9a9a98a7284ea2b05d9436a6d24fa` | Local/CI research baseline; its own status says AWS runtime and real artifacts were not accepted. |
| P2.6.5 hardening | `e23d04c341c4f6fe2ba081a5b0258c626df0c0c5` | Diverged from P2.6 full (2 left / 31 right); final behavior may be evidence, but `.p265-patch/*` staging debris is rejected. |
| P3 arbitrage lab | `4614d4a108797c79ef812182b48e07b1560d5304` | Model-free complete-set formulas, lifetime tracking, and deterministic two-leg replay are strong research sources. |
| P3 reconnect freshness | `86d5681b064d146b3f890625e9386c47951c5a93` | Adds transport/session freshness behavior; it diverges from the arbitrage-lab head, so only named files/tests are authoritative. |
| P3 replay clock fix | `a71390ad934c5592e5983912375f1acad9abce78` | Descends reconnect freshness by five commits and corrects replay to collector receive-time as-of state. |
| Independent PTB/Binance | `f0577f2daecf7e8d3740c0321c7290d142ae7987` | External-alpha separation is reusable; its 5m cohort and thresholds are not universal V3 policy. |
| Directional Edge V2 | `c128731b3dfb84cbdd6c283e621553f1e534574f` | Descends the independent-alpha and replay work. Useful paper evidence, but remains 5m-specific and cannot define twelve-bucket V3 thresholds. |
| Model safety gate | `b269a55a775772a2cb4017dd180e8590d157a757` | Fail-closed readiness is useful; broad exception suppression, pickle loading, and with-CLOB champion logic are rejected. |
| Restart paper reconciliation | `43c93670a4a73a927ecfb1f395991fed9eeda6f8` | Restart-safe OPEN reconciliation is reusable, subject to strict authoritative-result identity. |
| Empty-book truth | `fd7e624491d04a9615af9ce272783d73f7a1e137` | Empty asks are data, not missing-data defaults; preserve as a non-executable truthful state. |
| Session book seed | `6cbeb2f3a1850afc53ed66c32ca7c78d624d93e2` | Preserve source time while recording current-session observation time; both legs must be observed. |
| Book uptime/watchdog | `a626d91656ed6881d148bb310b41e38eba790183`, `c996d8151cb07ce099635a69c9ff36859c6fb052` | Bounded reconnect and external health evidence are reusable; old deployment scripts are not. |
| Dual40 hard-stop | `0071625599b408c8ddb99795b0a309a61e504c8f` | Diverges from Directional V2 and contains a 5/10/30 recovery ladder. It is quarantined to optional DUAL40 research and forbidden from Directional Edge. |

The Independent PTB/Binance head is an ancestor of Directional V2. P3 replay-clock is
also an ancestor of Directional V2. Dual40 diverges before the final Directional V2
commits, confirming that neither branch should be treated as a superset of the other.

## Capability migration decisions

Each row is the required `SOURCE → V3 OWNER → INVARIANTS → TESTS → DECISION` mapping.

### Public market feeds and discovery

| Capability | Source | V3 owner | Invariants | Tests to preserve/adapt | Decision |
|---|---|---|---|---|---|
| Binance trades/depth | `binance_feed.py`; gap/snapshot behavior on Directional V2 | `adapters/binance`, `market_data` | Exact asset map; source/receive clocks separate; bounded buffers; depth starts from snapshot; a sequence gap forces resync. | Snapshot-before-delta, gap resync, stale watchdog, symbol mismatch. | **REWRITE** on current public protocols. V3.2 already covers normalized trade/depth and gap state; full local-book restore belongs to V3.4. |
| Chainlink/reference | `chainlink_feed.py`, `reference/ref_chainlink.py`; boundary fixes `771303c`, `835def3` | `adapters/chainlink`, `market_data`, then `pricing/reference` in V3.4 | Market rules determine authority; proxy never substitutes; source/publisher/receive clocks remain distinct; exact decimal value; opening reference cannot be reconstructed from current mid-window value. | Fixed-point normalization, boundary-nearest point, mid-window restart fail-closed, silent-stream reconnect. | **REWRITE**. The old code sometimes prefers rounded float `value` and has historical source ambiguity. V3.2 uses current credential-free RTDS E18 wire data; authority mapping is deferred to V3.4. |
| Market discovery and identity | discovery changes around `1c51f61`, `2d9dc86`, `5d287d3`, `8d7a98e` | `adapters/polymarket`, `domain`, V3.4 canonical market engine | Condition/market/token identity explicit; outcomes mapped by labels; exact 5m/15m/1h windows; mismatches fail closed. | Deterministic BTC 5m/15m, 1h readable discovery, wrong outcome and wrong condition rejection. | **REWRITE** against current Gamma/CLOB schemas. V3.2 covers label/token association; canonical scheduling belongs to V3.4. |
| CLOB book persistence | `p26_book_store.py`; empty truth `fd7e624`; session seed `6cbeb2f`; observation fix `9bf73b0` | `market_data` then `storage` | Source-change time and receive-observation time are separate; identical reconnect snapshot refreshes observation without forging source time; empty book is persisted truth; both tokens seen in current session before use. | Empty-ask truth, identical reconnect, both-leg session seed, hash dedup, current-session completeness. | **PORT BEHAVIOR / REWRITE STORAGE** in V3.4 and V3.11. Do not copy the SQLite module. |
| Reconnect and watchdog | `p26_book_daemon_resilient*.py`, `p3_scanner_resilient.py`; `27b127d`, `c996d81` | `market_data`, `observability` | Reconnect does not imply freshness; planned rotation grace is bounded; session completeness required; dead transport vetoes recent rows. | Mature-session rotation only, incomplete session rejection, dead transport rejection, heartbeat expiry. | **PORT BEHAVIOR**. V3.2 supplies bounded reconnect and separate health/freshness; service supervision belongs to V3.13. |

### Pricing, models, and evaluation

| Capability | Source | V3 owner | Invariants | Tests to preserve/adapt | Decision |
|---|---|---|---|---|---|
| Dynamic CLOB fees | `p26_fee.py`, `test_p26_fee.py` at P3 lab | `pricing` with metadata from `adapters/polymarket` | Current per-market fee lineage required; fee is level-price dependent; token/condition match; missing schedule fails closed. | Dynamic multi-level fee, wrong-token rejection, missing-schedule rejection, formula version audit. | **REWRITE** in V3.5 using `Decimal` and current compact `mbf/tbf/fd` schema. Reject float money math and zero/default fee fallbacks. |
| Depth execution simulation | `p26_execution.py`, `test_p26_fee.py` | `pricing` / `execution` pure simulator | Consume actual levels; sizes are shares; record fill fraction, VWAP, worst price, impact, fees, all-in cost; empty depth is not a fill. | Multi-level partial/full fills, fee-per-share units, price impact, insufficient depth. | **PORT ALGORITHM / REWRITE TYPES** in V3.5 with immutable V3 books and `Decimal`. |
| External-only fair value | `p26_fair_value.py`, `p26_features.py`, tests on Directional V2 | `features`, `models` | Champion alpha excludes Polymarket prices; exact feature order/schema hash; frozen preprocessing; insufficient data/class balance yields no forecast. | Forbidden CLOB term, deterministic order, artifact/schema hash, class-balance gate. | **PORT ARCHITECTURE / REWRITE** in V3.7–V3.8. Reject `values.get(..., 0.0)` because missing features must not become zero. |
| Calibration | `p26_calibration.py`, `test_p26_calibration.py`, P2.6.5 README | `models`, `evaluation` | Past-only OOS calibration; no lookahead; fixed reliability buckets; Wilson uncertainty; DOWN bound derived conservatively. | Past-only calibration, insufficient bucket count, reliability bounds, deterministic output. | **PORT BEHAVIOR** in V3.8, split across exactly twelve asset/horizon buckets with fallback only when explicitly governed. |
| Walk-forward and promotion | `p26_walkforward.py`, `p26_promotion.py`, tests | `evaluation`, `replay` | Chronological clusters keep overlapping horizons together; purge/embargo; paired Brier/LogLoss; block bootstrap; drawdown/fold stability/concentration; paper-only promotion ceiling. | Partition disjointness, embargo, deterministic block bootstrap, worse-than-market rejection, concentration rejection. | **PORT BEHAVIOR / REWRITE** in V3.8/V3.12. No profitability or LIVE claim transfers from WhaleSignal. |
| Directional Edge V2 | commits `0df5d51`→`f0577f2`, strict work, then `a729a39` and `39b41f6`; head `c128731` | `strategies/directional`, `features`, `models`, `pricing` | External/official alpha chooses direction; Polymarket is valuation/execution only; direction lock; stale/uncalibrated means ABSTAIN; no recovery sizing. | Alpha-side-only valuation, CLOB-independent readiness, stability/flip gates, executable edge after costs. | **RESEARCH SOURCE, REWRITE** in V3.7. Reject copied 5m-only TTE, probability, ask, edge, and value thresholds until twelve-bucket PAPER evidence exists. |

### Risk, routing, execution, and storage

| Capability | Source | V3 owner | Invariants | Tests to preserve/adapt | Decision |
|---|---|---|---|---|---|
| Liquidity guard | `p26_liquidity_guard.py` | `risk` using `market_data` history | Stale/gapped/missing book veto; executable depth and persistence; transient/ghost liquidity wording does not allege intent. | Spread, depth, persistence, flicker, cancel/add, sequence gap. | **PORT BEHAVIOR / REWRITE** in V3.9. Rename any `SPOOFING` conclusion to risk evidence, not asserted manipulation. |
| Portfolio risk | `p26_portfolio_risk.py`, `test_p26_portfolio_risk.py` | `risk` | Total/per-asset/per-horizon/crypto-cluster exposure; overlap; bankroll; daily loss; drawdown; cooldown; global kill. | Every denial reason and boundary; correlated crypto cluster; fixed conservative stake. | **PORT POLICY / REWRITE PURE CORE** in V3.9. Remove SQLite and float coupling from the decision function. |
| Opportunity coordination | isolated scanners and later structural/Dual40 routing work | `router` | Capital/condition claims; expiry and reconciliation; unresolved one-leg lock; restart idempotency; no network methods. | Conflicts, double-spend, race, expired claims, one-leg lock. | **REWRITE** in V3.10. No coherent WhaleSignal common router is safe to copy. |
| Paper execution | `p26_paper_v2.py`, `p25_reconciled_paper_engine.py` | `execution`, `storage` | Same execution-plan contract as future LIVE; depth/fee fill simulation; partial fill; acknowledgement is not fill; decisions and outcomes auditable. | Partial/no fill, cancellation, restart idempotency, fee lineage, final position. | **PORT BEHAVIOR / REWRITE** in V3.11. Reject legacy order-shaped shortcuts and mutable cross-module state. |
| Reconciliation | `p25_paper_reconcile.py`, test at `43c9367`; uncertainty tests around `73b9a3f`, `8d3ba93` | `execution/reconciliation` | Exact immutable identity first; unresolved remains unresolved; uncertain response triggers scoped state/balance reconciliation; unknown is never flat or filled. | Stale OPEN after restart, condition mismatch, timeout/unknown response, missing balance observation. | **PORT INVARIANTS / REWRITE** in V3.11 and V3.14. Never copy order submission while migrating reconciliation. |
| Ledger | P2.6 paper recorder; `p3_live_ledger.py` | `storage` | Append/audit decision inputs, versions, risk, plan, fills, position, settlement; final state derived from reconciled evidence. | Idempotent event recording, ACK≠fill, realized PnL only after authoritative outcome, restart reconstruction. | **REWRITE** in V3.11. The WhaleSignal sidecar tables are evidence, not the V3 schema. |
| LIVE preflight | `p3_live_preflight.py`, `test_p3_live_guard.py`, network-uncertainty tests | `execution` and `trading-security` | Jurisdiction eligibility, credentials, auth probe, balance/allowance, explicit arm, restart unarmed, no order in preflight. | Missing secret, geoblock failure, insufficient balance, feature disabled, dry validation, uncertain response. | **DEFER AND REWRITE** in V3.14 against current official APIs. No usable order path is ported before the LIVE human gate. |

### Structural Arbitrage and DUAL40

| Capability | Source | V3 owner | Invariants | Tests to preserve/adapt | Decision |
|---|---|---|---|---|---|
| Complete-set parity | `p3_complete_set.py`, `p3_scanner.py`, tests at P3 lab head | `strategies/structural_arb`, `pricing` | Model-free; equal UP/DOWN shares; full-depth VWAP; dynamic fee; execution buffer; synchronized fresh pair; positive net economics. | BUY+MERGE and SPLIT+SELL formulas, breakpoint optimum, missing fee/book, stale/skewed pair. | **PORT ALGORITHM / REWRITE `Decimal`** in V3.6. No directional forecast dependency. |
| Opportunity lifetime | `p3_recorder.py`, `p3_scanner.py` | `strategies/structural_arb`, `storage` | Contiguous identity-stable windows; first/last/peak evidence; stale window closes; dedup. | Touch/close/peak and restart cases. | **PORT BEHAVIOR / REWRITE** in V3.6. |
| P3 latency and one-leg replay | `p3_replay.py`; corrected `p3_replay_clock.py` at `a71390a`; `test_p3_replay_clock.py` | `replay` | Event availability follows receive-time as-of; no source-time lookahead; 10/25/50/100/200/500 ms; partial/one-leg/unwind loss recorded. | Resting-book replay, both fill, one leg, no synchronous book, unwind feasibility, scheduler starvation. | **PORT CORRECTED BEHAVIOR / REWRITE** in V3.6/V3.12. Reject the legacy future-source-time lookup and broad replay-history deletion. |
| DUAL40 | `p3_dual40_*` on `0071625` and descendants | `strategies/dual40` only, optionally through `router` | Separate strategy kind; disabled by default; no strategy network calls; its recovery pool/5-10-30 ladder never enters Directional Edge. | Namespace isolation and router conflicts only if research is enabled. | **QUARANTINE / NO PORT IN PRE-LIVE CORE**. Recovery sizing is explicitly **REJECTED** for Directional Edge. |

## Explicit rejects

The following are not migration sources:

- `.p265-patch/*`, encoded patch chunks, CI transfer scaffolding, `*_v2.py`/`*_v3.py`
  duplicates, deployment hotfix branches, logs, runtime databases, and generated output.
- Deprecated or legacy Polymarket clients, credential handling, signing, live-order
  constructors, and one-click LIVE controls.
- Float-based money/probability calculations where exact decimal semantics matter.
- Missing-data coercion to `0`, current-value substitution for missing opening reference,
  and broad exception handlers that turn critical failures into plausible outputs.
- Directional V2’s empirical constants as V3 defaults; they are hypotheses for PAPER
  evaluation, not accepted 12-bucket policy.
- DUAL40’s recovery ladder anywhere outside its optional, isolated research namespace.

## V3.3 migration result

No WhaleSignal production module is copied or imported in V3.3. Clear behavior is
assigned to the V3 module that owns it and its regression tests are scheduled with that
implementation phase. This is deliberate: porting fee math before V3.5, structural math
before V3.6, directional policy before V3.7, or execution before V3.11 would create the
duplicate architecture forbidden by `AGENTS.md`.

V3.2 already independently implements several audited foundations on current protocols:
explicit source/receive timestamps, exact RTDS E18 parsing, outcome/token association,
bounded reconnect, source health/freshness, sequence-gap resync state, dynamic fee
metadata parsing, and public read-only transport. Later phases must cite the exact source
rows above when adapting further WhaleSignal behavior.
