import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OutcomeSide,
    ProxyReference,
)
from direction_engine_v3.features import ExternalDirectionalSnapshot, build_directional_features
from direction_engine_v3.market_data import (
    ChainlinkTwap,
    DataSource,
    EventLineage,
    MarketBucket,
    MarketDiscovery,
    PriceToBeatRecord,
    ReferenceFreshnessPolicy,
    SettlementMetadata,
    SettlementMethod,
    SQLitePriceToBeatRepository,
    official_reference_from_chainlink_twap,
)
from direction_engine_v3.market_data.official_runtime import (
    BinanceHourlyOfficialCollector,
    ChainlinkTwapCollector,
    OfficialPriceToBeatService,
)
from direction_engine_v3.shadow.daemon import ShadowDaemon, ShadowMarketState, new_evidence_window
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CHAINLINK_SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"
BINANCE_SOURCE = "https://www.binance.com/en/trade/BTC_USDT"


class FakeClock:
    def __init__(self, now: datetime = NOW + timedelta(milliseconds=40)) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return 123


class FakeTransport:
    def __init__(self, response: object) -> None:
        self.response = response

    async def get_json(self, _url: str, *, params: object | None = None) -> object:
        return self.response


def test_chainlink_collector_records_only_v3_60s_twap_and_selects_boundary() -> None:
    collector = ChainlinkTwapCollector(FakeTransport({}), FakeClock())

    wrong_topic = {
        "topic": "crypto_prices_twap_thirty",
        "type": "update",
        "timestamp": int(NOW.timestamp() * 1000),
        "payload": {
            "symbol": "btc/usd",
            "full_accuracy_value": "60000000000000000000000",
            "timestamp": int(NOW.timestamp() * 1000),
        },
    }
    assert collector.handle_message(wrong_topic) == 0

    valid = dict(wrong_topic)
    valid["topic"] = "crypto_prices_twap_sixty"
    assert collector.handle_message(valid) == 1
    selected = collector.opening_twap(
        Asset.BTC,
        boundary=NOW,
        tolerance=timedelta(seconds=1),
    )
    assert selected is not None
    assert selected.window_seconds == 60
    assert selected.value == Decimal("60000")
    assert collector.status().history_size_by_asset["BTC"] == 1


def test_ptb_service_establishes_and_restores_short_boundary(tmp_path: Path) -> None:
    asyncio.run(_assert_ptb_service_establishes_and_restores_short_boundary(tmp_path))


async def _assert_ptb_service_establishes_and_restores_short_boundary(tmp_path: Path) -> None:
    clock = FakeClock()
    collector = ChainlinkTwapCollector(FakeTransport({}), clock)
    collector.handle_message(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": int(NOW.timestamp() * 1000),
            "payload": {
                "symbol": "btc/usd",
                "full_accuracy_value": "60000000000000000000000",
                "timestamp": int(NOW.timestamp() * 1000),
            },
        }
    )
    repository = SQLitePriceToBeatRepository(tmp_path / "ptb.sqlite3")
    repository.initialize()
    service = OfficialPriceToBeatService(
        repository=repository,
        chainlink=collector,
        binance_hourly=BinanceHourlyOfficialCollector(FakeTransport({}), clock),
        policy=ReferenceFreshnessPolicy(
            timedelta(seconds=5),
            timedelta(seconds=1),
            timedelta(seconds=1),
        ),
    )
    discovery = _discovery(Horizon.FIVE_MINUTES)
    first = await service.resolve(discovery, observed_at=NOW + timedelta(seconds=2))
    assert first.ptb_status == "PTB_READY"
    assert first.reason == "PTB_ESTABLISHED"
    assert first.price_to_beat is not None
    assert first.price_to_beat.value == Decimal("60000")

    restored = await service.resolve(discovery, observed_at=NOW + timedelta(minutes=1))
    assert restored.ptb_status == "PTB_READY"
    assert restored.reason == "PTB_RESTORED"
    assert restored.restored is True


def test_ptb_service_rejects_missed_boundary_without_midwindow_fabrication(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_ptb_service_rejects_missed_boundary(tmp_path))


async def _assert_ptb_service_rejects_missed_boundary(tmp_path: Path) -> None:
    clock = FakeClock(NOW + timedelta(minutes=3))
    repository = SQLitePriceToBeatRepository(tmp_path / "ptb.sqlite3")
    repository.initialize()
    service = OfficialPriceToBeatService(
        repository=repository,
        chainlink=ChainlinkTwapCollector(FakeTransport({}), clock),
        binance_hourly=BinanceHourlyOfficialCollector(FakeTransport({}), clock),
        policy=ReferenceFreshnessPolicy(
            timedelta(seconds=5),
            timedelta(seconds=1),
            timedelta(seconds=1),
        ),
    )
    result = await service.resolve(_discovery(Horizon.FIVE_MINUTES), observed_at=clock.utc_now())
    assert result.ptb_status == "PTB_UNAVAILABLE"
    assert result.reason == "CHAINLINK_NO_MESSAGES"


def test_binance_1h_official_candle_requires_exact_window(tmp_path: Path) -> None:
    asyncio.run(_assert_binance_1h_official_candle_requires_exact_window(tmp_path))


async def _assert_binance_1h_official_candle_requires_exact_window(tmp_path: Path) -> None:
    clock = FakeClock(NOW + timedelta(milliseconds=40))
    raw_candle = [
        [
            int(NOW.timestamp() * 1000),
            "60000",
            "60100",
            "59900",
            "60050",
            "10",
        ]
    ]
    repository = SQLitePriceToBeatRepository(tmp_path / "ptb.sqlite3")
    repository.initialize()
    service = OfficialPriceToBeatService(
        repository=repository,
        chainlink=ChainlinkTwapCollector(FakeTransport({}), clock),
        binance_hourly=BinanceHourlyOfficialCollector(FakeTransport(raw_candle), clock),
        policy=ReferenceFreshnessPolicy(
            timedelta(seconds=5),
            timedelta(seconds=1),
            timedelta(seconds=1),
        ),
    )
    result = await service.resolve(_discovery(Horizon.ONE_HOUR), observed_at=clock.utc_now())
    assert result.ptb_status == "PTB_READY"
    assert result.official_reference is not None
    assert result.official_reference.source == BINANCE_SOURCE
    assert result.official_reference.value == Decimal("60000")


def test_daemon_passes_ready_ptb_past_official_unavailable_gate(tmp_path: Path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    for repository in (paper, shadow, corpus):
        repository.initialize()
    daemon = ShadowDaemon(
        data_client=_NoopDataClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=new_evidence_window(
            aws_user_id="user",
            aws_account="account",
            aws_arn="arn:aws:iam::123:root",
            started_at=NOW,
            commit="abcdef123456",
        ),
        report_dir=tmp_path,
        directional_corpus_repository=corpus,
    )
    discovery = _discovery(Horizon.FIVE_MINUTES)
    ptb = _ptb(discovery)
    state = ShadowMarketState(
        bucket=MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES),
        discovery=discovery,
        up_book=None,
        down_book=None,
        fee_schedule=None,
        proxy_reference=_proxy(discovery.market),
        official_reference=ptb.reference,
        observed_at=NOW + timedelta(minutes=1),
        price_to_beat=ptb,
        directional_features=_features(discovery.market, ptb),
        ptb_status="PTB_READY",
        ptb_reason="PTB_RESTORED",
        feature_status="FEATURES_READY",
    )
    daemon._evaluate_directional(state, cycle_id="cycle")
    event = shadow.latest_events(event_type="STRATEGY_EVALUATION", limit=1)[0]
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["ptb_status"] == "PTB_READY"
    assert payload["ptb_value"] == "60000"
    assert payload["reason"] == "MODEL_UNAVAILABLE"


class _NoopDataClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        raise AssertionError("not used")


def _discovery(horizon: Horizon) -> MarketDiscovery:
    market = _market(horizon)
    method = (
        SettlementMethod.BINANCE_CANDLE
        if horizon is Horizon.ONE_HOUR
        else SettlementMethod.CHAINLINK_TWAP
    )
    return MarketDiscovery(
        "event",
        "slug",
        "title",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            market.settlement_source,
            "rules",
            method,
            None if horizon is Horizon.ONE_HOUR else "btc-5m-twap-60",
            Asset.BTC,
            horizon,
            3600 if horizon is Horizon.ONE_HOUR else 60,
            "1",
            _lineage(DataSource.POLYMARKET_GAMMA),
        ),
    )


def _market(horizon: Horizon) -> Market:
    source = BINANCE_SOURCE if horizon is Horizon.ONE_HOUR else CHAINLINK_SOURCE
    duration = timedelta(hours=1) if horizon is Horizon.ONE_HOUR else timedelta(minutes=5)
    return Market(
        f"market-{horizon.value}",
        f"condition-{horizon.value}",
        Asset.BTC,
        horizon,
        (MarketToken("up", OutcomeSide.UP), MarketToken("down", OutcomeSide.DOWN)),
        NOW,
        NOW + duration,
        source,
    )


def _lineage(source: DataSource, source_ts: datetime = NOW) -> EventLineage:
    return EventLineage(
        source,
        source_ts,
        source_ts + timedelta(milliseconds=20),
        source_ts + timedelta(milliseconds=30),
        1,
    )


def _ptb(discovery: MarketDiscovery) -> PriceToBeatRecord:
    official = official_reference_from_chainlink_twap(
        discovery.market,
        ChainlinkTwap(
            Asset.BTC,
            Decimal("60000"),
            60,
            NOW,
            _lineage(DataSource.CHAINLINK_RTDS),
        ),
        recv_ts=NOW + timedelta(milliseconds=20),
        normalized_ts=NOW + timedelta(milliseconds=30),
        is_price_to_beat=True,
    )
    return PriceToBeatRecord(discovery.market.condition_id, official, NOW, "ptb:test")


def _proxy(market: Market) -> ProxyReference:
    return ProxyReference(
        "proxy",
        market.market_id,
        Asset.BTC,
        Decimal("60010"),
        "BINANCE_PUBLIC_DEPTH",
        NOW + timedelta(seconds=5),
        NOW + timedelta(seconds=5),
        NOW + timedelta(seconds=5),
    )


def _features(market: Market, ptb: PriceToBeatRecord):
    snapshot = ExternalDirectionalSnapshot(
        current_reference=_proxy(market),
        short_return=Decimal("0.001"),
        medium_return=Decimal("0.002"),
        momentum=Decimal("0.0005"),
        realized_volatility=Decimal("0.001"),
        volatility_acceleration=Decimal("0"),
        spot_perp_basis=Decimal("0"),
        external_book_imbalance=Decimal("0.1"),
        microprice_distance=Decimal("0.0001"),
        trade_imbalance=Decimal("0.2"),
        signal_stability=Decimal("0.9"),
        flip_rate=Decimal("0.1"),
        regime_score=Decimal("0.1"),
        source="test",
        source_ts=NOW + timedelta(seconds=5),
    )
    return build_directional_features(
        market,
        ptb,
        snapshot,
        generated_at=NOW + timedelta(minutes=1),
        feature_set_version="test",
    )
