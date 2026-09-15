# V3.10 Strategy Opportunity Router

The router arbitrates already-formed strategy candidates and emits at most one proposed
claim. It does not run strategy logic, perform risk approval, persist state, or submit an
order.

Every opportunity carries explicit condition/token identity, capital need,
risk-adjusted score, execution certainty, and candidate expiry. A versioned policy gives
strategy priority before within-strategy quality comparisons, avoiding direct selection
solely by heterogeneous raw return. DUAL40 remains disabled unless an explicit research
policy enables it.

Claims reserve condition, tokens, and capital under a stable candidate idempotency key.
The decision carries the snapshot revision so storage can atomically compare-and-set the
claim; a stale concurrent writer therefore cannot win a second reservation. Persisted
candidate IDs remain duplicates after restart.

Expired active claims do not release themselves. They produce
`CLAIM_RECONCILIATION_REQUIRED` until execution state is reconciled, after which the
pure reconciliation transition increments the snapshot revision. An unresolved
structural one-leg condition blocks every competing candidate on that condition.

WhaleSignal had no coherent common router suitable for migration, so V3.10 is a clean
rewrite behind the V3 interfaces. It contains no network methods or usable execution
path; PAPER defaults and disabled LIVE controls are unchanged.
