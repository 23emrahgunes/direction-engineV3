# P2.1 Feature Data Integrity + Temporal Dedup Audit

Status: `P2_1_EVIDENCE_INSUFFICIENT`

## Runtime-safety position

- PAPER/SHADOW only.
- No LIVE enablement, wallet/signing UI, order mutation endpoint, model promotion, stake change, risk-threshold change, settlement fabrication, or PAPER reset.
- Directional PAPER execution remains governed by P2.0 model governance; unpromoted models remain blocked.

## What changed

- P2.1A keeps production `ExternalTemporalState` on legacy append-all semantics.
- A separate diagnostic comparator state runs beside production on the same incoming events and rejects exact source-identity duplicates only for audit:
  - trades: source + asset + Binance aggregate trade id;
  - books: source + asset + update id; conflicting top-of-book payloads for the same update id are reported as `BOOK_IDENTITY_CONFLICT`;
  - references: source + asset + source timestamp + value + reference kind, with conflicting same-timestamp reference payloads reported as `REFERENCE_IDENTITY_CONFLICT`.
- Feature formulas are unchanged.
- `/api/feature/integrity` provides a bounded read-only P2.1A comparator report with duplicate/conflict counts, feature deltas, and diagnostic-only decision impact.
- The dashboard exposes P2.1 Feature Integrity as a manual/heavy panel; it is not part of initial page fan-out.
- Runtime production feature schema remains `v3.15.3-directional-official-ptb`.
- The conditional P2.1B schema `v3.15.3-directional-official-ptb-source-dedup-v2` is not written to production checkpoints in P2.1A.

## Evidence boundary

The implementation is intentionally comparator-only. Historical checkpoint rows do not contain raw Binance source ids, so historical materiality remains bounded by persisted feature/source timestamp integrity and future runtime diagnostics. Do not activate P2.1B production dedup unless P2.1A runtime evidence proves material feature or decision distortion, then CI, PAPER deploy, and 20+ post-deploy runtime cycles verify the correction.
