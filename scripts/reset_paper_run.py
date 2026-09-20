"""Explicit one-shot PAPER ledger reset admin command.

This script is intentionally not called by deploy automation. It archives only the
active PAPER ledger and initializes a fresh PAPER run after explicit confirmation.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from direction_engine_v3.app.dashboard import runtime_data_dir  # noqa: E402
from direction_engine_v3.config import (  # noqa: E402
    APP_MODE,
    LIVE_AUTO_ARM,
    LIVE_TRADING_ENABLED,
    PAPER_INITIAL_EQUITY_USDC,
)
from direction_engine_v3.storage import SQLitePaperRepository  # noqa: E402

PROJECT_UNITS: tuple[str, ...] = (
    "direction-engine-v3-shadow.service",
    "direction-engine-v3-dashboard.service",
    "direction-engine-v3-shadow-report.service",
    "direction-engine-v3-shadow-report.timer",
)

ZERO_COUNT_TABLES: tuple[str, ...] = (
    "paper_executions",
    "paper_trade_snapshots",
    "paper_trade_settlements",
    "paper_settlement_condition_attempts",
    "paper_trade_identity_overlays",
    "paper_corpus_label_tasks",
    "paper_abstains",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.confirm_paper_reset:
        raise SystemExit("refusing PAPER reset without --confirm-paper-reset")

    initial_equity = _parse_initial_equity(args.initial_equity)
    data_dir = Path(args.data_dir).resolve() if args.data_dir else runtime_data_dir().resolve()
    paper_path = data_dir / "paper.sqlite3"
    archive_dir = (
        Path(args.archive_dir).resolve() if args.archive_dir else (data_dir.parent / "archive")
    )
    started_at = datetime.now(UTC)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    paper_run_id = f"paper-{timestamp}-40usdc"
    archive_path: Path | None = None

    if args.manage_systemd:
        _stop_project_units()

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if paper_path.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / f"paper_before_clean_run_{timestamp}.sqlite3"
            _archive_active_paper_db(paper_path, archive_path)

        repository = SQLitePaperRepository(paper_path)
        repository.initialize()
        repository.save_run_metadata_once(
            paper_run_id=paper_run_id,
            initial_equity_usdc=initial_equity,
            started_at=started_at,
            archive_path=None if archive_path is None else str(archive_path),
            payload={
                "command": "scripts/reset_paper_run.py",
                "confirmed": True,
                "app_mode": APP_MODE,
                "live_trading_enabled": LIVE_TRADING_ENABLED,
                "live_auto_arm": LIVE_AUTO_ARM,
                "real_order_submission": False,
            },
        )
        summary = repository.summary(now=started_at)
        counts = repository.paper_table_counts()
        _verify_clean_run(summary, counts, initial_equity)
    except Exception:
        if archive_path is not None and archive_path.exists() and not paper_path.exists():
            archive_path.replace(paper_path)
        raise

    if args.restart_services:
        _start_project_units()

    _print_acceptance(
        archive_path=archive_path,
        paper_run_id=paper_run_id,
        summary=summary,
        counts=counts,
    )
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paper-reset", action="store_true")
    parser.add_argument("--initial-equity", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--archive-dir")
    parser.add_argument("--manage-systemd", action="store_true")
    parser.add_argument("--restart-services", action="store_true")
    return parser.parse_args(argv)


def _parse_initial_equity(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise SystemExit(f"invalid --initial-equity: {raw}") from exc
    expected = Decimal(PAPER_INITIAL_EQUITY_USDC)
    if value != expected or raw != PAPER_INITIAL_EQUITY_USDC:
        raise SystemExit(f"--initial-equity must be exactly {PAPER_INITIAL_EQUITY_USDC}")
    return value


def _archive_active_paper_db(paper_path: Path, archive_path: Path) -> None:
    if not paper_path.name == "paper.sqlite3":
        raise RuntimeError(f"refusing to archive unexpected PAPER DB path: {paper_path}")
    if archive_path.exists():
        raise RuntimeError(f"archive path already exists: {archive_path}")
    paper_path.replace(archive_path)
    try:
        _verify_sqlite_integrity(archive_path)
    except Exception:
        if archive_path.exists() and not paper_path.exists():
            archive_path.replace(paper_path)
        raise


def _verify_sqlite_integrity(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError(f"archive verification failed: missing or empty archive {path}")
    with sqlite3.connect(path) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None or str(row[0]).lower() != "ok":
        raise RuntimeError(f"archive integrity check failed for {path}")


def _verify_clean_run(
    summary: dict[str, object], counts: dict[str, int], initial_equity: Decimal
) -> None:
    expected = str(initial_equity)
    for key in (
        "initial_equity",
        "paper_current_equity",
        "raw_available_capital",
        "spendable_capital",
    ):
        if Decimal(str(summary.get(key))) != initial_equity:
            raise RuntimeError(f"clean PAPER run invariant failed for {key}: {summary.get(key)}")
    if str(summary.get("realized_pnl")) != "0":
        raise RuntimeError("clean PAPER run has non-zero realized PnL")
    if any(counts[table] != 0 for table in ZERO_COUNT_TABLES):
        raise RuntimeError(f"clean PAPER run contains non-zero ledger rows: {counts}")
    if expected != PAPER_INITIAL_EQUITY_USDC:
        raise RuntimeError("PAPER initial equity config mismatch")


def _stop_project_units() -> None:
    for unit in reversed(PROJECT_UNITS):
        subprocess.run(("systemctl", "stop", unit), check=False)


def _start_project_units() -> None:
    for unit in PROJECT_UNITS:
        subprocess.run(("systemctl", "start", unit), check=False)


def _print_acceptance(
    *,
    archive_path: Path | None,
    paper_run_id: str,
    summary: dict[str, object],
    counts: dict[str, int],
) -> None:
    print("PAPER_RESET_ACCEPTED")
    print(f"archive_path={archive_path if archive_path is not None else 'NONE'}")
    print(f"archive_integrity={'ok' if archive_path is not None else 'not_applicable'}")
    print(f"paper_run_id={paper_run_id}")
    print(f"initial_equity={summary['initial_equity']}")
    print(f"paper_current_equity={summary['paper_current_equity']}")
    print(f"raw_available_capital={summary['raw_available_capital']}")
    print(f"spendable_capital={summary['spendable_capital']}")
    print(f"open_trade_count={summary['open_trade_count']}")
    print(f"settled_trades={summary['settled_trades']}")
    print(f"wins={summary['wins']}")
    print(f"losses={summary['losses']}")
    print(f"realized_pnl={summary['realized_pnl']}")
    print(f"zero_ledger_counts={counts}")
    print(f"APP_MODE={APP_MODE}")
    print(f"LIVE_TRADING_ENABLED={str(LIVE_TRADING_ENABLED).lower()}")
    print(f"LIVE_AUTO_ARM={str(LIVE_AUTO_ARM).lower()}")
    print("real_order_submission=false")


if __name__ == "__main__":
    raise SystemExit(main())
