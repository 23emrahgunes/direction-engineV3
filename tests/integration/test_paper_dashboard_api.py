import asyncio
from datetime import UTC, datetime

from aiohttp.test_utils import make_mocked_request

from direction_engine_v3.app.server import (
    paper_abstains,
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
    trades = await paper_trades(make_mocked_request("GET", "/api/paper/trades?asset=BTC"))
    detail = await paper_trade_detail(
        make_mocked_request("GET", "/api/paper/trades/trade-1", match_info={"id": "trade-1"})
    )
    abstains = await paper_abstains(make_mocked_request("GET", "/api/paper/abstains"))

    assert summary.status == 200
    assert trades.status == 200
    assert detail.status == 200
    assert abstains.status == 200
    assert b"PAPER / SHADOW" in trades.body
    assert b"CALIBRATION_NOT_READY" in abstains.body
