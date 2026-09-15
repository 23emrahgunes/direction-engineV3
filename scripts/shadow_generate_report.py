"""Generate a deterministic V3.15 shadow report from the current frozen context."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from direction_engine_v3.shadow.service import run_once


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aws-user-id", required=True)
    parser.add_argument("--aws-account", required=True)
    parser.add_argument("--aws-arn", required=True)
    parser.add_argument("--data-dir", default="runtime/data")
    parser.add_argument("--report-dir", default="runtime/reports")
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
