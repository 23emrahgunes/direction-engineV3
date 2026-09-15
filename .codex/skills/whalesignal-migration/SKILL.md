---
name: whalesignal-migration
description: Migrates proven WhaleSignal P2.6/P3/Directional/Dual40 behavior into clean direction-engineV3 modules without bulk-copying branch debris. Use whenever reusing old repository code or tests.
---

# WhaleSignal Migration

WhaleSignal is a behavioral reference and source of proven tests.

Do not bulk-copy the repository into V3.

## Migration workflow

For each capability:

1. identify the target V3 owner/interface;
2. locate the WhaleSignal source implementation;
3. inspect relevant hardening branches and tests;
4. identify invariants and known historical bugs;
5. write/adapt V3 tests first when practical;
6. port the minimum logic;
7. update platform/API/SDK assumptions to current interfaces;
8. run parity/regression tests;
9. document provenance.

## High-value source areas

Expected reusable concepts include:

- Binance feed;
- Chainlink/reference routing;
- CLOB/book persistence;
- fee schedules;
- depth-aware execution simulation;
- fair-value model/calibration;
- liquidity guard;
- portfolio risk;
- P3 complete-set arbitrage;
- P3 replay/one-leg analysis;
- LIVE preflight/reconciliation/ledger patterns;
- Directional Edge V2 gates;
- DUAL40 balanced-regime research.

Treat exact file/branch selection as evidence-driven; do not assume the branch with the newest-looking name is canonical.

## Preserve separation

When porting:

- P3 structural arbitrage remains model-free;
- Directional Edge remains probability/fair-value based;
- DUAL40 remains optional/research;
- shared execution/risk infrastructure is extracted behind common interfaces.

## Historical bug classes to guard

Ensure regression coverage for:

- canonical TTE;
- PTB/reference availability;
- official vs proxy references;
- stale book/freshness;
- fee lineage;
- replay clock/lookahead;
- partial/FOK/FAK semantics;
- order reconciliation;
- duplicate submission;
- one-leg exposure.

## No legacy contamination

Do not carry forward:

- obsolete API clients without current verification;
- duplicate hotfix modules;
- dead deployment files;
- old secret/config values;
- broad fallback behavior that hides missing data.

Migration is complete only when V3 tests establish behavioral correctness.
