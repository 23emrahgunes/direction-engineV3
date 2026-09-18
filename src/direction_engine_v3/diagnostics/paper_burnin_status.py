"""Compact PAPER burn-in status diagnostic."""

import argparse
import json
from typing import Any

from direction_engine_v3.app.dashboard import build_paper_performance, runtime_data_dir
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Show Directional PAPER burn-in status.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of compact text")
    args = parser.parse_args()
    payload = _payload()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print("DIRECTIONAL PAPER")
    print("-----------------")
    summary = payload["performance"]["summary"]
    for key in (
        "initial_equity",
        "raw_available_capital",
        "spendable_capital",
        "open_cost_basis",
        "known_active_open_cost_basis",
        "known_expired_unsettled_cost_basis",
        "unknown_window_open_cost_basis",
        "expired_but_unsettled_cost_basis",
        "unfilled_reservations",
        "identity_recovered_count",
        "identity_blocked_count",
        "legacy_missing_window_end_count",
        "open_positions",
        "settlement_pending",
        "settled_trades",
        "wins",
        "losses",
        "win_rate",
        "realized_pnl",
        "roi",
        "maximum_drawdown",
    ):
        print(f"{key}: {summary.get(key)}")
    print("\nBUCKETS")
    for bucket in payload["performance"]["buckets"]:
        print(
            f"{bucket['asset']} {bucket['horizon']} "
            f"trades={bucket['total_trades']} open={bucket['open_positions']} "
            f"settled={bucket['settled_trades']} pnl={bucket['realized_pnl']}"
        )
    print("\nMODEL CORPUS")
    for key, counts in payload["corpus"].items():
        print(
            f"{key} observations={counts['observation_count']} "
            f"labeled={counts['labeled_observation_count']} "
            f"unique_markets={counts['unique_condition_count']} "
            f"labeled_unique={counts['labeled_unique_condition_count']}"
        )


def _payload() -> dict[str, Any]:
    data_dir = runtime_data_dir()
    paper = SQLitePaperRepository(data_dir / "paper.sqlite3")
    corpus = SQLiteDirectionalCorpusRepository(data_dir / "directional_corpus.sqlite3")
    paper.initialize()
    corpus.initialize()
    counts: dict[str, dict[str, int]] = {
        f"{bucket.asset.value}-{bucket.horizon.value}": corpus.corpus_counts(
            asset=bucket.asset, horizon=bucket.horizon
        )
        for bucket in SUPPORTED_MARKET_BUCKETS
    }
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "performance": build_paper_performance(strategy="DIRECTIONAL_EDGE"),
        "corpus": counts,
        "real_order_submission": False,
    }


if __name__ == "__main__":
    main()
