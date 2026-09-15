# V3.5 Pricing, Fees, and Execution Simulation

## Boundary

V3.5 is a pure `Decimal` calculation layer. It consumes normalized CLOB books and
identity-matched fee metadata and returns immutable estimates. It has no HTTP/WebSocket
client, credential handling, gateway, order builder, submit method, or venue side effect.

The source behavior was selectively adapted from the audited WhaleSignal P2.6 depth
simulation. Float money math, hard-coded/zero fallback fees, mutable SQLite coupling,
and website display-price shortcuts were rejected.

## Dynamic fees

The implementation follows the current official V2 fee formula:

`fee = shares * fee_rate * (price * (1 - price)) ** exponent`

The per-condition CLOB market-info response supplies the rate and exponent. A taker
calculation fails closed if either is absent, the condition differs, the metadata is
stale/future-dated, or the exponent cannot be evaluated as the supported integer form.
Maker cost is zero only when the retrieved schedule explicitly reports zero maker fee;
an unknown non-zero maker formula is rejected.

Each level fee is rounded to the documented five decimal USDC precision. The result
retains condition identity, formula version, role, inputs, and fee-schedule lineage.

Official references:

- <https://docs.polymarket.com/trading/fees>
- <https://docs.polymarket.com/v2-migration>
- <https://docs.polymarket.com/api-reference/market-data/get-clob-market-info>

## Depth simulation

- BUY consumes sorted asks no higher than the limit.
- SELL consumes sorted bids no lower than the limit.
- Price-dependent fees are calculated per consumed level.
- The result records executable depth, fills, fill fraction, gross notional, VWAP, worst
  price, adverse depth impact, fee, fee buffer, and slippage buffer.
- BUY reports conservative all-in cost per share; SELL reports conservative net proceeds
  per share.
- Insufficient depth is a partial fill. Empty/ineligible depth is a zero fill with no
  fabricated VWAP or price.
- Missing source time, stale/future book data, stale fee data, and condition mismatch
  fail closed.

The explicit input `limit_price` is an execution constraint against actual levels. No
website/display price is accepted as an executable-price input or result.

## Acceptance

Deterministic tests cover the official crypto fee table values, symmetry, precision,
fee lineage, maker/taker rules, multi-level BUY/SELL VWAP, partial and zero fills,
buffers, stale data, missing timestamps, and wrong identities. The VPS smoke test reads
the current BTC 5m Gamma identity, UP book, and CLOB fee metadata, then simulates only the
minimum order size. It performs no mutation and submits no order.
