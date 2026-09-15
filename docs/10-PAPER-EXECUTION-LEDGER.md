# V3.11 PAPER Execution, Reconciliation, and Ledger

`ExecutionPlan` remains the shared immutable contract. `PaperGateway` is the only
concrete gateway; `LiveGateway` is an interface with no implementation, authentication,
signing, endpoint, or order submission.

PAPER execution consumes explicit fill evidence rather than inventing market data. It
models acknowledged/no-fill, partial fill, and complete fill separately, applies the
global kill and reconciled-ledger checks again at execution time, and rejects LIVE or
expired plans. Acknowledgement never implies a fill.

SQLite stores the full plan and result as deterministic JSON under the plan idempotency
key, plus one immutable audit event. `BEGIN IMMEDIATE` and primary keys make concurrent
or restarted retries return the first durable result. No credential field exists in the
schema. Cancellation preserves already-filled quantity; uncertain reconciliation becomes
`UNKNOWN_ORDER_STATE` until authoritative fill evidence resolves it.

Settlement PnL is derived only from the typed official `Settlement` contract. This
selectively rewrites behavioral invariants from WhaleSignal P2.6 PAPER and P2.5
reconciliation rather than copying its mutable engines or order-shaped shortcuts.
