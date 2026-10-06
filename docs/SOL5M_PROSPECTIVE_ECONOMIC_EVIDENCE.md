# SOL-5m Prospective Economic Evidence

Status: `SOL5M_PROSPECTIVE_EVIDENCE_COLLECTING`

This phase collects forward-looking evidence for the frozen P2.3 SOL-5m
research challenger. It does not retrain, recalibrate, promote a model, open
PAPER trades, or enable LIVE.

- Current evidence schema: `SOL5M_PROSPECTIVE_EVIDENCE_V2`
- Legacy evidence schema: `SOL5M_PROSPECTIVE_EVIDENCE_V1`
- Asset / horizon: `SOL-5m`
- Checkpoint: `45s` TTE
- P2.3 cutoff: `2026-10-06T02:43:07.465344+00:00`
- P2.3 accepted tooling SHA: `b36a50d402ff5b384272607fb7b8352ffca3207a`
- P2.3 dataset fingerprint:
  `7759be2532b26d03f30c911bcba5928fab4521cd881528399e1613767f1e277f`
- Artifact status: `RESEARCH_ONLY`
- Promotion status: `NON_PROMOTABLE`
- Execution permission: `NONE`
- Governance rejection reason: `MODEL_NOT_PROMOTED`
- Research notional: `0.75 USDC`

Acceptance requires future-only rows after the P2.3 cutoff, checkpoint-time
executable pricing, and official outcomes attached through the existing
authoritative resolver path. Final GO/NO-GO is not revisited until at least 100
new unique labeled prospective SOL-5m conditions exist and pricing coverage is
at least 80%.

## Capture repair note

Runtime evidence from the initial V1 deployment showed prospective rows with
`pricing_status=PREDICTION_UNAVAILABLE` even when one side of executable pricing
was present. V2 separates prediction and pricing status so a frozen challenger
problem no longer hides contemporaneous pricing evidence.

- Root cause classification:
  `FROZEN_ARTIFACT_REPRODUCTION_REQUIRED` and
  `PRICING_COUPLED_TO_PREDICTION`
- Current frozen challenger blocker:
  `P2_3_DATASET_FINGERPRINT_MISMATCH`
- Artifact state: not promotable; prospective prediction remains fail-closed
  unless the exact frozen artifact is recovered or reproduced from the original
  P2.3 population.
- V1 rows are historical evidence only and are not backfilled.
- V2 rows expose `prediction_status`, `prediction_failure_reason`,
  `pricing_status`, `pricing_failure_reason`, `label_status`,
  `economic_eligible`, and `valid_capture_start`.

This repair is an evidence-capture repair only. It makes no profitability claim,
does not train or recalibrate a model, does not promote a model, and does not
enable PAPER or LIVE execution.
