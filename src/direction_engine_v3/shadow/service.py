"""V3.15 shadow service entrypoint.

The service initializes persistent evidence state and deterministic reports. It never signs,
arms LIVE, or submits orders.
"""

import argparse
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from direction_engine_v3.shadow.evidence import (
    AWSIdentityEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
)
from direction_engine_v3.shadow.reporting import build_shadow_summary, write_reports
from direction_engine_v3.shadow.storage import SQLiteShadowRepository


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _window(
    *,
    aws_user_id: str,
    aws_account: str,
    aws_arn: str,
    started_at: datetime,
) -> EvidenceWindow:
    commit = _git_commit()
    fingerprint = EvidenceFingerprint(
        code_commit=commit,
        strategy_version="v3.15-shadow-frozen",
        model_version="v3.8-unpromoted",
        calibration_version="v3.8-unpromoted",
        feature_schema_version="v3-directional-features",
        risk_policy_version="v3.9-risk",
        router_policy_version="v3.10-router",
        execution_policy_version="v3.11-paper",
        config_hash="paper-shadow-v3.15",
        artifact_hashes=(),
    )
    identity = AWSIdentityEvidence(aws_user_id, aws_account, aws_arn, started_at)
    return EvidenceWindow(
        window_id=f"shadow:{commit[:12]}:{int(started_at.timestamp())}",
        started_at=started_at,
        fingerprint=fingerprint,
        aws_identity=identity,
    )


def run_once(
    *,
    data_dir: Path,
    report_dir: Path,
    aws_user_id: str,
    aws_account: str,
    aws_arn: str,
) -> None:
    started_at = datetime.now(UTC)
    evidence_window = _window(
        aws_user_id=aws_user_id,
        aws_account=aws_account,
        aws_arn=aws_arn,
        started_at=started_at,
    )
    repository = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.save_window_once(
        window_id=evidence_window.window_id,
        payload=evidence_window.as_dict(),
        started_at=evidence_window.started_at,
    )
    repository.append_event(
        event_id=f"{evidence_window.window_id}:collector_started",
        window_id=evidence_window.window_id,
        event_type="COLLECTOR_STARTED",
        bucket_key=None,
        payload={"real_order_submission": False, "app_mode": "PAPER"},
        observed_at=started_at,
    )
    summary = build_shadow_summary(evidence_window=evidence_window, generated_at=started_at)
    write_reports(summary, report_dir)
    print(
        "shadow_status=EVIDENCE_ACCUMULATING "
        f"window_id={evidence_window.window_id} "
        f"aws_root={str(evidence_window.aws_identity.is_root).lower()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.environ.get("RUNTIME_DATA_DIR", "runtime/data"))
    parser.add_argument(
        "--report-dir", default=os.environ.get("RUNTIME_REPORT_DIR", "runtime/reports")
    )
    parser.add_argument("--aws-user-id", required=True)
    parser.add_argument("--aws-account", required=True)
    parser.add_argument("--aws-arn", required=True)
    args = parser.parse_args()
    run_once(
        data_dir=Path(args.data_dir),
        report_dir=Path(args.report_dir),
        aws_user_id=args.aws_user_id,
        aws_account=args.aws_account,
        aws_arn=args.aws_arn,
    )


if __name__ == "__main__":
    main()
