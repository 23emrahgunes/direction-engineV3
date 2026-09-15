---
name: trading-security
description: Protects trading credentials, LIVE controls, dashboard mutation paths, network actions, and fail-closed operational boundaries. Use for auth, secrets, LIVE execution, deployment, or operator controls.
---

# Trading Security

## Secrets

Never:

- commit `.env`;
- print private keys;
- print API secret/passphrase;
- include secrets in exception strings;
- store secrets in SQLite/analytics tables;
- expose secrets through dashboard/API;
- put secrets in shell history when avoidable.

Redact sensitive configuration in diagnostics.

## LIVE safety

Defaults:

- LIVE disabled;
- auto-arm disabled;
- restart returns to unarmed unless explicitly designed otherwise and approved;
- paper/research commands cannot implicitly enable live.

Require an explicit, auditable arming path before state-changing LIVE orders.

## Authorization

Operator mutation endpoints must use appropriate authentication and CSRF protection where browser sessions are used.
Bind internal dashboards to localhost by default.
Use TLS/reverse proxy if exposed beyond trusted local access.

## Geographic/platform restrictions

Do not implement mechanisms to bypass platform restrictions, geoblocks, account restrictions, or applicable legal/contractual limits.

If LIVE eligibility cannot be verified, fail closed.

## Network mutation

Before a state-changing order call, recheck:

- LIVE armed;
- kill switch;
- market active;
- token mapping;
- order constraints;
- data freshness;
- risk approval;
- candidate not expired;
- idempotency key not consumed.

## Dependencies

Pin/audit critical signing and trading dependencies.
Do not copy an old SDK solely because legacy code used it.

## Incident state

On unknown position/order state or unresolved residual exposure:

- block new LIVE trades;
- preserve evidence;
- reconcile;
- require operator review when configured.
