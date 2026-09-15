"""Print current V3.15 shadow report status."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    data = Path("runtime/data/shadow_evidence.sqlite3")
    paper = Path("runtime/data/paper.sqlite3")
    report = Path("runtime/reports/shadow_summary.json")
    event_counts: dict[str, int] = {}
    abstains = 0
    trades = 0
    if data.exists():
        with sqlite3.connect(data) as connection:
            rows = connection.execute(
                "SELECT event_type, COUNT(*) FROM shadow_events GROUP BY event_type"
            ).fetchall()
            event_counts = {str(row[0]): int(row[1]) for row in rows}
    if paper.exists():
        with sqlite3.connect(paper) as connection:
            row = connection.execute("SELECT COUNT(*) FROM paper_abstains").fetchone()
            abstains = int(row[0]) if row is not None else 0
            row = connection.execute("SELECT COUNT(*) FROM paper_trade_snapshots").fetchone()
            trades = int(row[0]) if row is not None else 0
    if not report.exists():
        print(
            "shadow_status=MISSING_REPORT "
            f"events={json.dumps(event_counts, sort_keys=True)} "
            f"abstains={abstains} trades={trades}"
        )
        return
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
    encoded_events = json.dumps(event_counts, sort_keys=True)
    print(
        f"shadow_status={payload.get('status')} buckets={len(buckets)} "
        f"states={encoded_states} events={encoded_events} abstains={abstains} trades={trades}"
    )


if __name__ == "__main__":
    main()
