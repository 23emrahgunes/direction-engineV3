---
name: python-realtime-systems
description: Designs reliable Python asyncio/WebSocket services for low-latency market data and trading workflows. Use for feeds, daemons, queues, reconnects, background tasks, shutdown, and concurrency bugs.
---

# Python Realtime Systems

Prefer explicit async ownership and bounded concurrency.

## Event-loop rules

- Do not block the event loop with synchronous network/file/database work.
- Use timeouts around external I/O.
- Use bounded queues where producers can outrun consumers.
- Define a backpressure/drop/coalesce policy for high-frequency market data.
- Keep one clear owner for each long-lived task.
- Make cancellation and shutdown deterministic.
- Use `asyncio.TaskGroup` or equivalent structured concurrency when supported.

## WebSocket lifecycle

Implement:

1. connect;
2. authenticate/subscribe if required;
3. snapshot/bootstrap when the protocol requires it;
4. process messages;
5. heartbeat/ping-pong;
6. detect stale transport;
7. reconnect with bounded exponential backoff + jitter;
8. resubscribe;
9. restore book/state consistency;
10. publish health.

A TCP/WebSocket connection being open does not prove the data is fresh.

## State safety

Keep separate:

- connection status;
- subscription status;
- last transport receive time;
- last valid source event time;
- current semantic state.

Do not mark a feed healthy only because reconnect succeeded.

## Time

Use wall-clock UTC for persisted timestamps.
Use monotonic clocks for elapsed time, timeout, heartbeat, and latency measurement.

## Error handling

Classify errors:

- transient transport;
- authentication/configuration;
- protocol/schema;
- stale/sequence;
- data-quality;
- fatal invariant.

Retry only errors that are genuinely retryable.
Configuration/auth/invariant failures should fail visibly, not loop forever.
