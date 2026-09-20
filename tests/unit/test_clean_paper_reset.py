import gc
import importlib.util
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from direction_engine_v3.storage import SQLitePaperRepository

ROOT = Path(__file__).resolve().parents[2]
RESET_SCRIPT = ROOT / "scripts" / "reset_paper_run.py"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _load_reset_module():
    spec = importlib.util.spec_from_file_location("reset_paper_run", RESET_SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fresh_paper_repository_summary_uses_40_usdc_default(tmp_path):
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()

    summary = repository.summary(now=NOW)

    assert summary["initial_equity"] == "40.00"
    assert summary["paper_current_equity"] == "40.00"
    assert summary["raw_available_capital"] == "40.00"
    assert summary["spendable_capital"] == "40.00"
    assert summary["realized_pnl"] == "0"
    assert summary["open_trade_count"] == 0
    assert summary["settled_trades"] == 0
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert summary["paper_run_label"] == "40 USDC CLEAN BURN-IN"


def test_reset_script_refuses_missing_confirmation(tmp_path):
    reset = _load_reset_module()

    with pytest.raises(SystemExit):
        reset.main(["--initial-equity", "40.00", "--data-dir", str(tmp_path)])

    assert not (tmp_path / "paper.sqlite3").exists()


def test_reset_script_requires_exact_40_usdc_initial_equity(tmp_path):
    reset = _load_reset_module()

    with pytest.raises(SystemExit):
        reset.main(
            [
                "--confirm-paper-reset",
                "--initial-equity",
                "40.0",
                "--data-dir",
                str(tmp_path),
            ]
        )

    assert not (tmp_path / "paper.sqlite3").exists()


def test_reset_script_archives_existing_paper_db_and_initializes_clean_run(
    tmp_path, capsys
):
    data_dir = tmp_path / "runtime" / "data"
    data_dir.mkdir(parents=True)
    untouched = {
        data_dir / "directional_corpus.sqlite3": b"corpus",
        data_dir / "shadow_evidence.sqlite3": b"shadow",
        data_dir / "price_to_beat.sqlite3": b"ptb",
        tmp_path / "runtime" / "reports" / "report.json": b"report",
    }
    for path, content in untouched.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    repository = SQLitePaperRepository(data_dir / "paper.sqlite3")
    repository.initialize()
    repository.save_trade_snapshot(
        trade_id="trade-1",
        decision_id="decision-1",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-1",
        side="UP",
        status="OPEN",
        payload={
            "stake": "1",
            "cost_basis_usdc": "1",
            "real_order_submission": False,
        },
        observed_at=NOW,
    )
    del repository
    gc.collect()

    reset = _load_reset_module()
    exit_code = reset.main(
        ["--confirm-paper-reset", "--initial-equity", "40.00", "--data-dir", str(data_dir)]
    )
    output = capsys.readouterr().out
    fresh = SQLitePaperRepository(data_dir / "paper.sqlite3")

    assert exit_code == 0
    assert "PAPER_RESET_ACCEPTED" in output
    assert "initial_equity=40.00" in output
    assert "paper_current_equity=40.00" in output
    assert "raw_available_capital=40.00" in output
    assert "spendable_capital=40.00" in output
    assert "real_order_submission=false" in output
    archives = list((tmp_path / "runtime" / "archive").glob("paper_before_clean_run_*.sqlite3"))
    assert len(archives) == 1
    with sqlite3.connect(archives[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM paper_trade_snapshots").fetchone()[0] == 1
    assert all(path.read_bytes() == content for path, content in untouched.items())
    assert fresh.summary(now=NOW)["initial_equity"] == "40.00"
    assert fresh.summary(now=NOW)["paper_run_id"] is not None
    assert all(count == 0 for count in fresh.paper_table_counts().values())


def test_reset_script_restores_active_db_when_archive_verification_fails(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "runtime" / "data"
    repository = SQLitePaperRepository(data_dir / "paper.sqlite3")
    repository.initialize()
    before = (data_dir / "paper.sqlite3").read_bytes()
    del repository
    gc.collect()
    reset = _load_reset_module()

    def fail_integrity(_path):
        raise RuntimeError("forced integrity failure")

    monkeypatch.setattr(reset, "_verify_sqlite_integrity", fail_integrity)

    with pytest.raises(RuntimeError, match="forced integrity failure"):
        reset.main(
            ["--confirm-paper-reset", "--initial-equity", "40.00", "--data-dir", str(data_dir)]
        )

    assert (data_dir / "paper.sqlite3").exists()
    assert (data_dir / "paper.sqlite3").read_bytes() == before
    assert not list((tmp_path / "runtime" / "archive").glob("paper_before_clean_run_*.sqlite3"))


def test_paper_run_id_is_attached_to_trade_and_settlement_payloads(tmp_path):
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    metadata = repository.save_run_metadata_once(
        paper_run_id="run-40",
        initial_equity_usdc=Decimal("40.00"),
        started_at=NOW,
        archive_path=None,
        payload={"source": "test"},
    )
    trade = repository.save_trade_snapshot(
        trade_id="trade-1",
        decision_id="decision-1",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-1",
        side="UP",
        status="OPEN",
        payload={"real_order_submission": False},
        observed_at=NOW,
    )
    settlement = repository.save_settlement_once(
        settlement_id="settlement-1",
        trade_id="trade-1",
        condition_id="condition-1",
        official_winning_side="UP",
        selected_side="UP",
        settlement_source_kind="OFFICIAL",
        settlement_source="POLYMARKET_GAMMA_MARKET_ID_AND_CLOB_CONDITION",
        official_resolved_at=NOW,
        official_resolution_observed_at=NOW,
        settled_at=NOW,
        filled_shares=Decimal("1"),
        cost_basis_usdc=Decimal("0.50"),
        payout_usdc=Decimal("1"),
        realized_paper_pnl=Decimal("0.50"),
        win_loss="WIN",
        evidence_hash="hash",
        payload={"real_order_submission": False},
    )

    assert metadata.paper_run_id == "run-40"
    assert trade.payload["paper_run_id"] == "run-40"
    assert settlement.payload["paper_run_id"] == "run-40"


def test_normal_deploy_script_does_not_invoke_paper_reset():
    deploy_script = (ROOT / "deploy" / "aws" / "paper_deploy.sh").read_text(encoding="utf-8")

    assert "reset_paper_run.py" not in deploy_script
