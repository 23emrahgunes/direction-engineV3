---
name: execution-engineering
description: Designs PAPER/LIVE execution plans, order state machines, maker/taker selection, idempotency, fill reconciliation, partial-fill handling, one-leg unwind, and ledger correctness.
---

# Execution Engineering

Strategy code emits `ExecutionPlan`; gateways execute it.

## Common plan

An execution plan should capture:

- strategy/version;
- condition/token/side;
- target shares or stake;
- order type/time-in-force;
- limit/worst acceptable price;
- expected VWAP/all-in cost;
- expected fee;
- maximum slippage/impact;
- candidate expiry;
- risk decision ID;
- idempotency/cycle key.

## PAPER and LIVE

Use one plan contract:

`ExecutionPlan → PaperGateway`
or
`ExecutionPlan → LiveGateway`

Do not duplicate strategy logic inside gateway implementations.

## State machine

Model explicit states such as:

DISCOVERED
→ CANDIDATE
→ RISK_APPROVED
→ PLANNED
→ SUBMITTING
→ ACKNOWLEDGED
→ PARTIAL/FILLED
→ RECONCILING
→ POSITION
→ SETTLED/CLOSED

Exceptional states:

- REJECTED
- CANCELLED
- EXPIRED
- UNKNOWN_ORDER_STATE
- ONE_LEG_EXPOSURE
- UNWINDING
- LIVE_HALTED

## Idempotency

Protect against duplicate submits after:

- timeout;
- reconnect;
- process restart;
- retry;
- duplicate strategy event.

A network timeout means "unknown", not "not submitted".

## Reconciliation

Order acknowledgement is not a fill.

Reconcile using the strongest available evidence:

- authenticated order/fill endpoints/events;
- token/position/balance deltas where appropriate;
- final exchange state.

Persist ambiguity and halt when exposure cannot be determined safely.

## Maker/taker

Choose maker/taker from net economics and time/risk constraints.
Do not blindly chase price.

## Structural two-leg handling

For non-atomic two-leg strategies, precompute one-leg stress/unwind feasibility.
If one leg remains exposed, follow a bounded unwind procedure and halt on unresolved residual risk.

## Retry policy

Retries must be bounded and idempotent.
Never loop indefinitely on order submission/cancellation.
