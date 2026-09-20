"""Compact PAPER burn-in status diagnostic."""

import argparse
import json
from typing import Any

from direction_engine_v3.app.dashboard import build_paper_performance, runtime_data_dir
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.shadow.storage import ShadowStorageUnavailable, SQLiteShadowRepository
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
        "paper_run_id",
        "paper_run_label",
        "paper_run_started_at",
        "paper_run_archive_path",
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
        "raw_snapshot_missing_window_end_count",
        "effective_identity_missing_trade_count",
        "identity_recovered_condition_count",
        "settlement_queue_due_condition_count",
        "settlement_pending_condition_count",
        "settlement_blocked_condition_count",
        "last_attempted_condition",
        "last_attempt_reason",
        "last_successful_settlement_at",
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
    print("\nRECENT SETTLEMENT SCANS")
    for event in payload["recent_settlement_scans"]:
        event_payload = event["payload"]
        print(
            f"{event['observed_at']} condition={event_payload.get('last_attempted_condition')} "
            f"checked={event_payload.get('settlement_checked')} "
            f"pending={event_payload.get('settlement_pending')} "
            f"blocked={event_payload.get('settlement_blocked')} "
            f"completed={event_payload.get('settlement_completed')} "
            f"error={event_payload.get('last_settlement_error')}"
        )


def _payload() -> dict[str, Any]:
    data_dir = runtime_data_dir()
    paper = SQLitePaperRepository(data_dir / "paper.sqlite3")
    corpus = SQLiteDirectionalCorpusRepository(data_dir / "directional_corpus.sqlite3")
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3", read_only=True)
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
        "recent_settlement_scans": _recent_settlement_scans(shadow),
        "real_order_submission": False,
    }


def _recent_settlement_scans(
    shadow: SQLiteShadowRepository,
) -> tuple[dict[str, object], ...]:
    try:
        return shadow.latest_events(event_type="PAPER_SETTLEMENT_SCAN", limit=5)
    except ShadowStorageUnavailable:
        return ()


if __name__ == "__main__":
    main()
