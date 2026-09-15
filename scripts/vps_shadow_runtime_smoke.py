"""Read-only V3.15.1 shadow runtime smoke over persisted runtime evidence."""

import json
import sqlite3
from pathlib import Path


def _counts(path: Path, table: str, column: str) -> dict[str, int]:
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def main() -> None:
    shadow_db = Path("runtime/data/shadow_evidence.sqlite3")
    paper_db = Path("runtime/data/paper.sqlite3")
    shadow_counts = _counts(shadow_db, "shadow_events", "event_type")
    abstain_counts = _counts(paper_db, "paper_abstains", "reason")
    if shadow_counts.get("REAL_SHADOW_CYCLE", 0) < 1:
        raise SystemExit("shadow_runtime_smoke=FAIL missing_REAL_SHADOW_CYCLE")
    if sum(abstain_counts.values()) < 1:
        raise SystemExit("shadow_runtime_smoke=FAIL missing_abstain_records")
    output = {
        "shadow_runtime_smoke": "PASS",
        "shadow_events": shadow_counts,
        "abstains": abstain_counts,
        "paper_trades": sum(_counts(paper_db, "paper_trade_snapshots", "status").values()),
        "label": "PAPER / SHADOW — NO REAL ORDER",
    }
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
