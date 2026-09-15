import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon, Market, MarketToken, OutcomeSide
from direction_engine_v3.market_data import (
    OFFICIAL_REFERENCE_SOURCES,
    DataSource,
    EventLineage,
    FeeSchedule,
    MarketBucket,
    MarketDiscovery,
    PolymarketBook,
    PolymarketLevel,
    SettlementMetadata,
    SettlementMethod,
)
from direction_engine_v3.shadow.daemon import ShadowDaemon, ShadowMarketState, new_evidence_window
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)


class StaticClock:
    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        return 1


class FixtureClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        if bucket != MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES):
            return ShadowMarketState(bucket, None, None, None, None, None, None, now, "fixture")
        return _market_state(bucket)


def test_shadow_daemon_records_evaluations_abstains_and_paper_trade(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    first = asyncio.run(daemon.run_once())
    second = asyncio.run(daemon.run_once())

    assert first.markets_discovered == 1
    assert first.book_observations == 2
    assert first.strategy_evaluations >= 3
    assert first.abstain_records >= 11
    assert first.paper_trades == 1
    assert second.paper_trades == 1
    assert len(paper.trades()) == 1
    assert paper.trades()[0].label == "PAPER / SHADOW — NO REAL ORDER"
    assert paper.summary()["open_positions"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1


def _market_state(bucket: MarketBucket) -> ShadowMarketState:
    market = Market(
        "market-btc-5m",
        "condition-btc-5m",
        bucket.asset,
        bucket.horizon,
        (
            MarketToken("token-up", OutcomeSide.UP),
            MarketToken("token-down", OutcomeSide.DOWN),
        ),
        NOW,
        NOW + timedelta(minutes=5),
        OFFICIAL_REFERENCE_SOURCES[bucket],
    )
    lineage = EventLineage(DataSource.POLYMARKET_CLOB, NOW, NOW, NOW, 1)
    discovery = MarketDiscovery(
        "event-btc-5m",
        "btc-updown-5m-1799877600",
        "BTC up or down",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            market.settlement_source,
            "rules",
            SettlementMethod.CHAINLINK_TWAP,
            "btc-5m-twap-60",
            bucket.asset,
            bucket.horizon,
            60,
            "1",
            lineage,
        ),
    )
    up = PolymarketBook(
        market.condition_id,
        "token-up",
        (PolymarketLevel(Decimal("0.39"), Decimal("10")),),
        (PolymarketLevel(Decimal("0.40"), Decimal("10")),),
        "hash-up",
        Decimal("5"),
        Decimal("0.01"),
        lineage,
    )
    down = PolymarketBook(
        market.condition_id,
        "token-down",
        (PolymarketLevel(Decimal("0.39"), Decimal("10")),),
        (PolymarketLevel(Decimal("0.40"), Decimal("10")),),
        "hash-down",
        Decimal("5"),
        Decimal("0.01"),
        lineage,
    )
    fee = FeeSchedule(
        market.condition_id,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("2"),
        lineage,
    )
    return ShadowMarketState(bucket, discovery, up, down, fee, None, None, NOW)
