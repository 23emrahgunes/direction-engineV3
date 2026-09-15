# V3.4 Canonical Market, PTB, and Reference Engine

## Scope and ownership

The engine supports exactly the Cartesian product of BTC, ETH, SOL, and XRP with 5m,
15m, and 1h. `MarketBucket` and `SUPPORTED_MARKET_BUCKETS` are the single twelve-bucket
enumeration. Canonical timing and reference validation live in `market_data`; Gamma
schema translation remains in the Polymarket adapter; immutable identities remain in
`domain`.

No component in this phase submits orders, chooses a strategy, or supplies fake data.

## Canonical windows and TTE

- 5m, 15m, and 1h windows use exact UTC duration and epoch alignment.
- Gamma `eventStartTime` is the trading-window start. Gamma `startDate` is creation/open
  metadata and is intentionally not used for TTE.
- TTE is computed from the already identity-checked `CanonicalWindow`.
- TTE before the window or at/after expiry is an error rather than a clamped value.
- The current epoch slug convention is generated only for 5m/15m. Hourly markets use a
  separate human-readable vendor slug and must be discovered as such.

## Discovery and settlement identity

Canonical acceptance cross-checks event slug, asset, horizon, event start/end,
condition/market/token identity, settlement source, settlement method, and source
configuration. Outcomes are mapped from explicit `Up`/`Down` labels and never by an
assumed array position.

Current public rules differ by horizon:

- 5m and 15m use the asset-specific Chainlink 60-second TWAP stream and require the
  matching `{asset}-{horizon}-twap-60` configuration.
- 1h uses the asset-specific Binance USDT one-hour candle named by the market rules and
  has no Chainlink TWAP configuration.

This distinction is rule-defined authority. A Binance observation typed as
`ProxyReference` remains a proxy and cannot satisfy an official 1h reference requirement.

## Price to beat and restart safety

An opening price to beat can be established only from `OfficialReference` data whose
market, asset, and rule-defined source match discovery. Its effective time must match the
window boundary, source age and receive latency must pass policy, and establishment must
occur within the boundary tolerance.

A mid-window process may restore a previously persisted boundary-proven
`PriceToBeatRecord`. It may not reconstruct PTB from a current official value, a proxy, a
zero/default, or the latest available quote. The fixed opening PTB is not treated as a
current-price freshness sample; current official and proxy freshness are evaluated on
separate typed paths.

## Acceptance

Unit/regression coverage includes all twelve buckets, exact alignment/duration, wrong
TTE context, creation-time confusion, outcome/config/source/slug/window mismatches,
stale and late official data, proxy substitution, and mid-window restore identity. The
VPS probe performs read-only Gamma validation of the live current market in every bucket
and records reference method, source, TTE, condition identity, and request latency.
