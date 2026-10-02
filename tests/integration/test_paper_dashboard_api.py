import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiohttp.test_utils import make_mocked_request

from direction_engine_v3.app.server import (
    paper_abstains,
    paper_reconciliation,
    paper_summary,
    paper_trade_detail,
    paper_trades,
)
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)


def test_paper_dashboard_api_reads_canonical_repository(tmp_path, monkeypatch) -> None:
    asyncio.run(_assert_paper_dashboard_api_reads_canonical_repository(tmp_path, monkeypatch))


async def _assert_paper_dashboard_api_reads_canonical_repository(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    repository.save_trade_snapshot(
        trade_id="trade-1",
        decision_id="decision-1",
        strategy="STRUCTURAL_ARBITRAGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-1",
        side="BUY_MERGE",
        status="OPEN",
        payload={"net_edge": "0.5", "fee": "0", "stake": "4"},
        observed_at=NOW,
    )
    repository.save_abstain(
        abstain_id="abstain-1",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-1",
        reason="CALIBRATION_NOT_READY",
        payload={"label": "PAPER / SHADOW — NO REAL ORDER"},
        observed_at=NOW,
    )

    summary = await paper_summary(make_mocked_request("GET", "/api/paper/summary"))
    reconciliation = await paper_reconciliation(
        make_mocked_request("GET", "/api/paper/reconciliation")
    )
    trades = await paper_trades(make_mocked_request("GET", "/api/paper/trades?asset=BTC"))
    detail = await paper_trade_detail(
        make_mocked_request("GET", "/api/paper/trades/trade-1", match_info={"id": "trade-1"})
    )
    abstains = await paper_abstains(make_mocked_request("GET", "/api/paper/abstains"))

    assert summary.status == 200
    assert reconciliation.status == 200
    assert trades.status == 200
    assert detail.status == 200
    assert abstains.status == 200
    assert b"PAPER / SHADOW" in trades.body
    assert b"CALIBRATION_NOT_READY" in abstains.body


def test_paper_reconciliation_explains_raw_overlay_and_window_exposure(tmp_path) -> None:
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    now = NOW
    repository.save_trade_snapshot(
        trade_id="directional-active",
        decision_id="decision-1",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-active",
        side="UP",
        status="OPEN",
        payload={
            "cost_basis_usdc": "1.25",
            "stake": "1.25",
            "window_start": (now - timedelta(minutes=1)).isoformat(),
            "window_end": (now + timedelta(minutes=4)).isoformat(),
        },
        observed_at=now - timedelta(minutes=1),
    )
    repository.save_trade_snapshot(
        trade_id="structural-active",
        decision_id="decision-2",
        strategy="STRUCTURAL_ARBITRAGE",
        asset="ETH",
        horizon="15m",
        condition_id="condition-structural",
        side="BUY_MERGE",
        status="OPEN",
        payload={
            "cost_basis_usdc": "2.50",
            "window_start": (now - timedelta(minutes=2)).isoformat(),
            "window_end": (now + timedelta(minutes=12)).isoformat(),
        },
        observed_at=now - timedelta(minutes=2),
    )
    repository.save_trade_snapshot(
        trade_id="expired-unsettled",
        decision_id="decision-3",
        strategy="DIRECTIONAL_EDGE",
        asset="SOL",
        horizon="5m",
        condition_id="condition-expired",
        side="DOWN",
        status="OPEN",
        payload={
            "cost_basis_usdc": "0.75",
            "window_start": (now - timedelta(minutes=10)).isoformat(),
            "window_end": (now - timedelta(minutes=5)).isoformat(),
        },
        observed_at=now - timedelta(minutes=10),
    )
    repository.save_settlement_condition_attempt(
        condition_id="condition-expired",
        state="PENDING",
        attempted_at=now,
        next_attempt_at=now + timedelta(minutes=5),
        reason="AWAITING_OFFICIAL_RESULT",
    )
    repository.save_trade_snapshot(
        trade_id="unknown-window",
        decision_id="decision-4",
        strategy="DIRECTIONAL_EDGE",
        asset="XRP",
        horizon="1h",
        condition_id="condition-unknown",
        side="UP",
        status="OPEN",
        payload={"cost_basis_usdc": "0.50"},
        observed_at=now - timedelta(minutes=3),
    )
    repository.save_trade_snapshot(
        trade_id="raw-open-settled",
        decision_id="decision-5",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="15m",
        condition_id="condition-settled",
        side="DOWN",
        status="OPEN",
        payload={
            "cost_basis_usdc": "3.00",
            "window_end": (now - timedelta(minutes=1)).isoformat(),
        },
        observed_at=now - timedelta(minutes=15),
    )
    repository.save_settlement_once(
        settlement_id="settlement-raw-open",
        trade_id="raw-open-settled",
        condition_id="condition-settled",
        official_winning_side="UP",
        selected_side="DOWN",
        settlement_source_kind="OFFICIAL",
        settlement_source="POLYMARKET_GAMMA_MARKET_ID_AND_CLOB_CONDITION",
        official_resolved_at=now,
        official_resolution_observed_at=now,
        settled_at=now,
        filled_shares=Decimal("5"),
        cost_basis_usdc=Decimal("3.00"),
        payout_usdc=Decimal("0"),
        realized_paper_pnl=Decimal("-3.00"),
        win_loss="LOSS",
        evidence_hash="settlement-hash",
        payload={},
    )

    payload = repository.exposure_reconciliation(now=now)

    assert payload["status"] == "RECONCILIATION_OK"
    assert payload["summary"]["open_trade_count"] == 4
    assert payload["summary"]["open_cost_basis"] == "5.00"
    assert payload["calculated"]["global_open_trade_count"] == 4
    assert payload["calculated"]["global_open_cost_basis"] == "5.00"
    assert payload["calculated"]["directional_open_count"] == 3
    assert payload["calculated"]["directional_open_cost_basis"] == "2.50"
    assert payload["calculated"]["structural_open_count"] == 1
    assert payload["calculated"]["structural_open_cost_basis"] == "2.50"
    assert payload["calculated"]["expired_unsettled_count"] == 1
    assert payload["calculated"]["unknown_window_open_count"] == 1
    summary = repository.summary(now=now)
    assert summary["directional_open_trade_count"] == 3
    assert summary["directional_open_cost_basis"] == "2.50"
    assert summary["structural_open_trade_count"] == 1
    assert summary["structural_open_cost_basis"] == "2.50"
    assert summary["structural_unknown_window_open_cost_basis"] == "0"
    rows = {str(item["trade_id"]): item for item in payload["trades"]}
    assert rows["raw-open-settled"]["raw_status"] == "OPEN"
    assert rows["raw-open-settled"]["overlay_status"] == "SETTLED"
    assert rows["raw-open-settled"]["exposure_bucket"] == "legacy_stale_open_snapshot"
    assert rows["expired-unsettled"]["window_state"] == "EXPIRED_UNSETTLED"
    assert rows["expired-unsettled"]["settlement_attempt_state"] == "PENDING"
    assert rows["unknown-window"]["exposure_bucket"] == "unknown_window_open"


def test_paper_reconciliation_api_is_read_only_get(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    repository.save_trade_snapshot(
        trade_id="trade-1",
        decision_id="decision-1",
        strategy="STRUCTURAL_ARBITRAGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-1",
        side="BUY_MERGE",
        status="OPEN",
        payload={"cost_basis_usdc": "4.00", "window_end": (NOW + timedelta(minutes=5)).isoformat()},
        observed_at=NOW,
    )

    response = asyncio.run(
        paper_reconciliation(make_mocked_request("GET", "/api/paper/reconciliation"))
    )
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["real_order_submission"] is False
    assert payload["status"] == "RECONCILIATION_OK"
    assert payload["calculated"]["structural_open_cost_basis"] == "4.00"


def test_paper_trades_api_defaults_and_caps_large_read_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    for index in range(130):
        repository.save_trade_snapshot(
            trade_id=f"trade-{index:03d}",
            decision_id=f"decision-{index:03d}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=f"condition-{index:03d}",
            side="UP",
            status="OPEN",
            payload={
                "cost_basis_usdc": "0.10",
                "window_end": (NOW + timedelta(minutes=5)).isoformat(),
            },
            observed_at=NOW + timedelta(seconds=index),
        )

    default_response = asyncio.run(paper_trades(make_mocked_request("GET", "/api/paper/trades")))
    capped_response = asyncio.run(
        paper_trades(make_mocked_request("GET", "/api/paper/trades?limit=999"))
    )
    default_payload = json.loads(default_response.text)
    capped_payload = json.loads(capped_response.text)

    assert default_response.status == 200
    assert capped_response.status == 200
    assert len(default_payload["trades"]) == 25
    assert len(capped_payload["trades"]) == 100
    assert default_payload["real_order_submission"] is False
    assert capped_payload["real_order_submission"] is False


def test_paper_reconciliation_api_caps_large_detail_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    for index in range(260):
        repository.save_trade_snapshot(
            trade_id=f"trade-{index:03d}",
            decision_id=f"decision-{index:03d}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=f"condition-{index:03d}",
            side="UP",
            status="OPEN",
            payload={
                "cost_basis_usdc": "0.10",
                "window_end": (NOW + timedelta(minutes=5)).isoformat(),
            },
            observed_at=NOW + timedelta(seconds=index),
        )

    response = asyncio.run(
        paper_reconciliation(make_mocked_request("GET", "/api/paper/reconciliation"))
    )
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["calculated"]["raw_snapshot_count"] == 260
    assert payload["open_trades_total_count"] == 260
    assert payload["trades_total_count"] == 260
    assert payload["open_trades_truncated"] is True
    assert payload["trades_truncated"] is True
    assert len(payload["open_trades"]) == 100
    assert len(payload["trades"]) == 250
