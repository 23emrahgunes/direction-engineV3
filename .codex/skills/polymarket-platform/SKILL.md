---
name: polymarket-platform
description: Implements and reviews Polymarket market discovery, CLOB data, fees, authentication, orders, user events, settlement, and conditional-token operations. Use for any Polymarket adapter or execution integration.
---

# Polymarket Platform

Treat Polymarket integration as a protocol boundary.

## Freshness requirement

Before implementing or changing an API/SDK integration, verify the current official
Polymarket documentation/SDK. Do not assume an old client, endpoint, auth flow, fee
formula, or order response remains current.

Do not introduce a deprecated client merely because WhaleSignal used it historically.

## Market discovery

Persist stable identifiers separately:

- condition/event/market identity as applicable;
- outcome token IDs;
- outcome side mapping;
- start/end timestamps;
- resolution/settlement source metadata;
- tick/min order constraints;
- fee metadata.

Never infer UP/DOWN token identity from list position without validating outcome labels.

## CLOB

Maintain canonical normalized:

- bids/asks;
- source timestamp when available;
- receive timestamp;
- sequence/hash/version when available;
- tick size;
- minimum order constraints;
- best bid/ask;
- depth.

Reject impossible books and crossed/invalid states unless the protocol explicitly explains them.

## Fees

Never hardcode a fee assumption into strategy math when fee data can vary.
Obtain and persist fee lineage with:

- enabled/disabled state;
- parameters/rate/exponent as applicable;
- source;
- retrieval time;
- formula/version.

If a required fee schedule is unavailable, pricing-sensitive execution must fail closed.

## Orders

Separate:

- intent;
- signed request;
- submit result;
- exchange order ID;
- acknowledgement;
- partial fill;
- full fill;
- cancellation;
- final reconciliation.

An API response indicating acceptance is not proof of position.

## User/private channel

Where available, use authenticated user/order events to reduce reconciliation delay, but
always retain a REST/state reconciliation path after reconnect or ambiguity.

## Settlement / CTF

Keep settlement/conditional-token operations isolated from ordinary strategy logic.
Merge, split, redeem, and balance verification must be explicit state transitions.

## Security

Never log signing material, private keys, API secrets, passphrases, or raw auth headers.
