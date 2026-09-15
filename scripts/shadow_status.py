"""Print current V3.15 shadow report status."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    report = Path("runtime/reports/shadow_summary.json")
    if not report.exists():
        raise SystemExit("shadow_status=MISSING_REPORT")
    payload = json.loads(report.read_text(encoding="utf-8"))
    buckets = payload.get("buckets")
    if not isinstance(buckets, list):
        raise SystemExit("shadow_status=INVALID_REPORT")
    states: dict[str, int] = {}
    for bucket in buckets:
        if isinstance(bucket, dict):
            state = str(bucket.get("promotion_state"))
            states[state] = states.get(state, 0) + 1
    encoded_states = json.dumps(states, sort_keys=True)
    print(f"shadow_status={payload.get('status')} buckets={len(buckets)} states={encoded_states}")


if __name__ == "__main__":
    main()
