"""Run the P2.3 SOL-5m offline GO/NO-GO experiment.

The script is read-only with respect to runtime SQLite state.  It writes only
the requested offline report/artifact files when explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from direction_engine_v3.evaluation import render_markdown_report, run_sol5m_go_no_go


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("runtime/data/directional_corpus.sqlite3"),
        help="Path to directional_corpus.sqlite3",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/P2_3_SOL_5M_GO_NO_GO.md"),
        help="Markdown report output path",
    )
    parser.add_argument(
        "--minimum-final-conditions",
        type=int,
        default=30,
        help="Minimum final holdout unique conditions before evidence is meaningful",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional compact JSON summary output path for SSM-safe inspection",
    )
    parser.add_argument(
        "--artifact-output",
        type=Path,
        default=None,
        help="Optional full P2.3R research artifact JSON output path",
    )
    args = parser.parse_args()
    code_sha = _git_sha()
    result = run_sol5m_go_no_go(
        args.db,
        code_sha=code_sha,
        minimum_final_conditions=args.minimum_final_conditions,
    )
    report = render_markdown_report(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(_summary(result.as_dict()), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.artifact_output:
        if result.research_artifact is None:
            raise RuntimeError("P2.3R research artifact was not produced")
        args.artifact_output.parent.mkdir(parents=True, exist_ok=True)
        args.artifact_output.write_text(
            json.dumps(result.research_artifact, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(report)
    return 0


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _summary(result: dict[str, object]) -> dict[str, object]:
    dataset = result.get("dataset")
    spec = result.get("final_challenger_spec")
    final = result.get("final_challenger")
    base = result.get("final_base_rate")
    return {
        "marker": result.get("marker"),
        "verdict": result.get("verdict"),
        "dataset": dataset if isinstance(dataset, dict) else {},
        "selected_checkpoint": result.get("selected_checkpoint"),
        "selected_regularization": result.get("selected_regularization"),
        "final_challenger_spec": spec if isinstance(spec, dict) else {},
        "final_challenger": final if isinstance(final, dict) else None,
        "final_base_rate": base if isinstance(base, dict) else None,
        "uncertainty": result.get("uncertainty"),
        "failed_gates": result.get("failed_gates"),
        "gate_table": result.get("gate_table"),
        "final_evaluation_count": result.get("final_evaluation_count"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
