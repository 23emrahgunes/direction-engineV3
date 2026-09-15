# V3.6 Structural Arbitrage

## Strategy boundary

Structural Arbitrage is a model-free complete-set strategy. The scanner consumes only a
canonical binary market, synchronized UP/DOWN CLOB books, current per-condition fee
metadata, and explicit conservative policies. It does not import a forecast/model,
Directional Edge, a network transport, or an execution gateway.

The implementation selectively rewrites the P3 complete-set and corrected replay
behavior audited at WhaleSignal revisions `4614d4a108797c79ef812182b48e07b1560d5304`
and `a71390ad934c5592e5983912375f1acad9abce78`. Float arithmetic, legacy source-time
lookahead, database deletion, and any order submission were rejected.

## Complete-set economics

### BUY+MERGE

Equal quantities of UP and DOWN are bought from actual ask depth. Per-level dynamic
taker fees, depth VWAP, fee buffer, slippage buffer, and a cycle buffer are included.
The complete set pays one unit. Both legs must be fully executable at the target size and
the buffered net profit must meet policy.

### SPLIT+SELL

Research support prices an existing complete set by selling equal UP/DOWN quantities
into actual bid depth. Net proceeds include the same dynamic fees and buffers. This is a
pure estimate: V3.6 does not split tokens, merge tokens, hold collateral, or submit an
order.

Book condition/token identity, both source timestamps, both receive timestamps, pair
skew, individual freshness, fee identity, and full paired depth all fail closed.

## Opportunity lifetime

`OpportunityLifetimeTracker` records contiguous condition/action windows with first and
last observation, peak expected profit, observation count, and explicit closure. It is
an in-memory pure state object in V3.6; durable ownership arrives with the V3.11 ledger.

## Deterministic latency replay

`replay_buy_merge` runs the governed 10, 25, 50, 100, 200, and 500 ms matrix. The first
and second legs are sequential. Book availability is selected strictly by local receive
time, preventing a source-timestamp event that arrived later from leaking backward.

Every result records both-leg completion, each leg's fill, matched complete sets,
partial fill, unmatched one-leg exposure, unwind feasibility/proceeds/loss, and net cycle
PnL. An infeasible unwind remains explicit and is never converted into a flat position.

## Acceptance boundary

Deterministic fixtures are test inputs, not production fallback data. VPS public
validation reads one current BTC 5m identity, both books, and fee metadata, then runs
BUY+MERGE and SPLIT+SELL scans. A non-opportunity due to net economics is a valid
fail-closed result; the probe performs no remote mutation.
