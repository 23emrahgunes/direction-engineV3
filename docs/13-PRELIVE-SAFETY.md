# V3.14 PRE-LIVE Safety

V3.14 adds a PRE-LIVE safety gate without adding a live order implementation.

The gate denies by default unless all required conditions are true:

- LIVE trading is explicitly enabled and armed
- auto-arm is not enabled
- kill switch is clear
- market, token mapping, freshness, and risk approval are valid
- idempotency key is unused
- no unresolved order state or residual exposure exists
- credentials, geo eligibility, and account eligibility are verified

The repository still contains no real Polymarket order submission path.
