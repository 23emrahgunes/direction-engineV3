# V3.15.1 Real SHADOW/PAPER Runtime Correction

V3.15.1 corrects the V3.15 evidence pipeline from report-only infrastructure to a
persistent SHADOW/PAPER runtime. The previous V3.15 window recorded only
`COLLECTOR_STARTED` and deterministic report refresh evidence; it must not be counted as
valid strategy burn-in.

The corrected runtime keeps the same repository owners:

- public data remains under `adapters/*` and `market_data/*`;
- strategy modules remain pure and never submit orders;
- router and risk decide before execution planning;
- `ExecutionPlan` is sent only to `PaperGateway`;
- canonical PAPER visibility is read from `SQLitePaperRepository`;
- dashboard/API endpoints are read-only.

The corrected evidence window starts only after VPS smoke proves real public market
discovery, Polymarket book observations, proxy/reference observations, real strategy
evaluations, and ABSTAIN/rejection records are accumulating. Natural PAPER trades are
recorded only when accepted strategy/router/risk conditions produce them; thresholds are
not lowered to force a trade.

Safety state remains:

- `APP_MODE=PAPER`
- `LIVE_TRADING_ENABLED=false`
- `LIVE_AUTO_ARM=false`
- `real_order_submission=false`

No signing, authenticated order submission, LIVE gateway implementation, LIVE arming, or
real Polymarket order path is added in V3.15.1.
