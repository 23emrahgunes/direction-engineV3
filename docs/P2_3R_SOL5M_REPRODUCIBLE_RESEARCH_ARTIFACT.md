# P2.3R — SOL-5m Reproducible Research Artifact

## Decision

The legacy SOL-5m P2.3 frozen artifact is treated as irrecoverable:
`SOL5M_FROZEN_ARTIFACT_HISTORY_IRRECOVERABLE`.

The previous report/summary files contained metrics and dataset evidence, but
not a loadable artifact with coefficients, scaler parameters, Platt calibrator
parameters, feature order, and a self-verifying artifact checksum. The recovered
production corpus also differs from the original frozen population by one
checkpoint row, so the old model is not repaired or approximated.

## P2.3R behavior

P2.3R creates a new reproducible SOL-5m research artifact from the current
read-only corpus using the same offline safety discipline:

- `RESEARCH_ONLY`
- `NON_PROMOTABLE`
- `execution_permission=NONE`
- no PAPER execution
- no LIVE execution
- no model promotion
- no runtime training or recalibration

The offline runner can now write a full artifact JSON via:

```bash
python scripts/p23_sol5m_go_no_go.py \
  --db runtime/data/directional_corpus.sqlite3 \
  --output docs/P2_3_SOL_5M_GO_NO_GO.md \
  --summary-output runtime/model_artifacts/sol5m_p23r_summary.json \
  --artifact-output runtime/model_artifacts/sol5m_p23r_research_artifact.json
```

The summary report is not accepted as an artifact. Runtime prospective capture
loads only `runtime/model_artifacts/sol5m_p23r_research_artifact.json` through
the validated artifact loader. If the artifact is missing or invalid, prediction
fails closed and pricing evidence capture remains independent.

## Artifact contents

The artifact includes:

- dataset manifest and manifest checksum;
- condition IDs, checkpoint row IDs, observed timestamps, feature order, schema
  versions, cutoff, UP/DOWN counts, chronological split;
- L2 logistic coefficients and intercept;
- scaler means/scales;
- Platt calibrator coefficient/intercept;
- selected checkpoint, regularization, feature list, excluded features;
- final metrics and GO/NO-GO gate results;
- artifact checksum over the canonical payload.

## Runtime boundary

The artifact's `valid_capture_start` is the latest observed timestamp in the
artifact dataset. Prospective evidence rows before or at that boundary are not
captured under the new artifact. Existing V1/V2 broken rows are preserved and
not backfilled.
