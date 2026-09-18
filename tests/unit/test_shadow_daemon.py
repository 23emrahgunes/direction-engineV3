import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OutcomeSide,
    ProxyReference,
)
from direction_engine_v3.features import ExternalDirectionalSnapshot, build_directional_features
from direction_engine_v3.market_data import (
    OFFICIAL_REFERENCE_SOURCES,
    DataSource,
    EventLineage,
    FeeSchedule,
    MarketBucket,
    MarketDiscovery,
    PolymarketBook,
    PolymarketLevel,
    PriceToBeatRecord,
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


class SettlementProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self) -> dict[str, object]:
        self.calls += 1
        return {
            "settlement_checked": 0,
            "settlement_pending": 0,
            "settlement_completed": 0,
            "settlement_blocked": 0,
            "last_settlement_check_at": NOW.isoformat(),
            "last_settlement_error": None,
        }


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
    assert first.paper_trades == 2
    assert second.paper_trades == 1
    trades = paper.trades()
    assert len(trades) == 2
    assert any(
        item.reason == "DIRECTIONAL_POSITION_ALREADY_OPEN" for item in paper.abstains()
    )
    directional = next(item for item in trades if item.strategy == "DIRECTIONAL_EDGE")
    assert directional.label == "PAPER / SHADOW — NO REAL ORDER"
    assert directional.payload["model_version"] == "PAPER_RESEARCH_BASELINE"
    assert directional.payload["real_order_submission"] is False
    assert paper.summary()["open_positions"] == 2
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1


def test_negative_paper_capital_abstains_without_crashing_and_keeps_settlement_scan(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    paper.save_trade_snapshot(
        trade_id="existing-filled-over-budget",
        decision_id="decision:existing",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-existing",
        side="UP",
        status="OPEN",
        observed_at=NOW - timedelta(minutes=1),
        payload={
            "condition_id": "condition-existing",
            "market_id": "market-existing",
            "stake": "1200",
            "cost_basis_usdc": "1200",
            "shares": "1200",
            "window_end": (NOW + timedelta(minutes=4)).isoformat(),
            "fill_status": "FILLED",
            "real_order_submission": False,
        },
    )
    probe = SettlementProbe()
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
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert probe.calls == 1
    assert result.markets_discovered == 1
    capital_abstain = next(
        item for item in paper.abstains() if item.reason == "PAPER_CAPITAL_DEFICIT"
    )
    assert capital_abstain.payload["raw_available_capital"] == "-200"
    assert capital_abstain.payload["spendable_capital"] == "0"
    directional_trades = paper.trades(strategy="DIRECTIONAL_EDGE", limit=100)
    assert len(directional_trades) == 1
    summary = paper.summary(now=NOW)
    assert Decimal(str(summary["raw_available_capital"])) < Decimal("0")
    assert summary["spendable_capital"] == "0"
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1


def test_router_snapshot_does_not_reserve_filled_positions_twice(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    paper.save_trade_snapshot(
        trade_id="filled-open",
        decision_id="decision:filled",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-filled",
        side="UP",
        status="OPEN",
        observed_at=NOW,
        payload={
            "stake": "10",
            "cost_basis_usdc": "10",
            "window_end": (NOW + timedelta(minutes=5)).isoformat(),
            "fill_status": "FILLED",
            "real_order_submission": False,
        },
    )
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    snapshot = daemon._router_snapshot_from_paper(NOW)
    portfolio = daemon._portfolio_state_from_paper(NOW)

    assert snapshot.claims == ()
    assert portfolio.bankroll_available == Decimal("990")
    assert tuple(item.capital_at_risk for item in portfolio.exposures) == (Decimal("10"),)


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
    official = OfficialReference(
        "official-btc-5m",
        market.market_id,
        bucket.asset,
        Decimal("60000"),
        market.settlement_source,
        NOW,
        NOW,
        NOW,
        True,
    )
    proxy = ProxyReference(
        "proxy-btc-5m",
        market.market_id,
        bucket.asset,
        Decimal("60600"),
        "BINANCE_PROXY",
        NOW,
        NOW,
        NOW,
    )
    ptb = PriceToBeatRecord(
        market.condition_id,
        official,
        NOW,
        "ptb:btc:5m:condition-btc-5m",
    )
    features = build_directional_features(
        market,
        ptb,
        ExternalDirectionalSnapshot(
            proxy,
            Decimal("0.010"),
            Decimal("0.012"),
            Decimal("0.010"),
            Decimal("0.001"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0.20"),
            Decimal("0.010"),
            Decimal("0.20"),
            Decimal("0.90"),
            Decimal("0.05"),
            Decimal("0.20"),
            "BINANCE_EXTERNAL_FEATURES",
            NOW,
        ),
        generated_at=NOW,
        feature_set_version="v3.15.4-test-features",
    )
    return ShadowMarketState(
        bucket,
        discovery,
        up,
        down,
        fee,
        proxy,
        official,
        NOW,
        price_to_beat=ptb,
        directional_features=features,
        ptb_status="PTB_READY",
        ptb_reason="PTB_ESTABLISHED",
        feature_status="FEATURES_READY",
    )
