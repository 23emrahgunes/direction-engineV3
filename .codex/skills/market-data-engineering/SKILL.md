---
name: market-data-engineering
description: Builds canonical, timestamp-correct, freshness-aware market data pipelines across Binance, Chainlink/official references, and Polymarket. Use for feeds, normalization, PTB/reference state, book caches, and data-quality bugs.
---

# Market Data Engineering

Trading logic may only consume data with explicit lineage and freshness.

## Required timestamps

Carry separately where possible:

- `source_ts_ms`
- `recv_ts_ms`
- `normalized_ts_ms`
- `decision_ts_ms`

Never overwrite source time with receive time.

## Freshness

Define source-specific freshness budgets in configuration.

Examples of distinct concepts:

- transport alive;
- most recent message received;
- source event fresh;
- official reference fresh;
- order book fresh;
- UP/DOWN pair synchronized.

A feed can be connected but stale.

## Sequence and ordering

For streams with sequence/version information:

- detect gaps;
- detect duplicates;
- detect out-of-order events;
- restore from a fresh snapshot when required.

Do not continue using a known-corrupt incremental book.

## Deduplication

Deduplicate with protocol identifiers/sequence/timestamps where possible.
Do not deduplicate independent economically meaningful events simply because prices are equal.

## Official vs proxy

Maintain strict types:

`OfficialReference`
- source used by market rules/settlement or authoritative PTB/current reference.

`ProxyReference`
- fast external market source used as predictive information.

Proxy data must never silently fill a missing official value.

## PTB

A PTB value must include:

- value;
- source;
- canonical market/window identity;
- effective timestamp;
- retrieval lineage;
- validity/freshness state.

Do not calculate distance-to-PTB when PTB identity is uncertain.

## Persistence

Persist enough raw/normalized state to reproduce a decision without relying on future data.
