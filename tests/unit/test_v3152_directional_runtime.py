from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OutcomeSide,
    ProxyReference,
)
from direction_engine_v3.features import ExternalTemporalState
from direction_engine_v3.market_data import (
    BinanceHourlyCandle,
    ChainlinkTwap,
    DataSource,
    EventLineage,
    MarketBucket,
    PriceToBeatIdentity,
    ReferenceFreshnessPolicy,
    SQLitePriceToBeatRepository,
    establish_price_to_beat,
    official_reference_from_binance_hourly_candle,
    official_reference_from_chainlink_twap,
    reject_proxy_as_official,
)
from direction_engine_v3.market_data.contracts import (
    CryptoTopOfBook,
    CryptoTrade,
    MarketDiscovery,
    SettlementMetadata,
    SettlementMethod,
)
from direction_engine_v3.models import ShadowModelState, empty_registry
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CHAINLINK_SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"
BINANCE_SOURCE = "https://www.binance.com/en/trade/BTC_USDT"


def _market(horizon: Horizon, source: str = CHAINLINK_SOURCE) -> Market:
    duration = {
        Horizon.FIVE_MINUTES: timedelta(minutes=5),
        Horizon.FIFTEEN_MINUTES: timedelta(minutes=15),
        Horizon.ONE_HOUR: timedelta(hours=1),
    }[horizon]
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


def _proxy(reference_id: str, value: str, source_ts: datetime = NOW) -> ProxyReference:
    return ProxyReference(
        reference_id,
        "market-5m",
        Asset.BTC,
        Decimal(value),
        "BINANCE_PROXY",
        source_ts,
        source_ts,
        source_ts,
    )


def test_proxy_cannot_construct_official_reference_or_ptb(tmp_path) -> None:
    proxy = _proxy("proxy", "60000")
    with pytest.raises(TypeError, match="ProxyReference"):
        reject_proxy_as_official(proxy)

    repo = SQLitePriceToBeatRepository(tmp_path / "ptb.sqlite3")
    repo.initialize()
    assert repo.get(
        PriceToBeatIdentity(
            Asset.BTC,
            Horizon.FIVE_MINUTES,
            "market-5m",
            "condition-5m",
            NOW,
            CHAINLINK_SOURCE,
        )
    ) is None


def test_official_routing_for_short_and_hourly_buckets_and_wrong_source_rejection() -> None:
    short_market = _market(Horizon.FIVE_MINUTES)
    twap = ChainlinkTwap(Asset.BTC, Decimal("60000"), 60, NOW, _lineage(DataSource.CHAINLINK_RTDS))
    official = official_reference_from_chainlink_twap(
        short_market,
        twap,
        recv_ts=NOW + timedelta(milliseconds=20),
        normalized_ts=NOW + timedelta(milliseconds=30),
        is_price_to_beat=True,
    )
    assert official.source == CHAINLINK_SOURCE
    assert official.is_price_to_beat is True

    hourly_market = _market(Horizon.ONE_HOUR, BINANCE_SOURCE)
    candle = BinanceHourlyCandle(
        Asset.BTC,
        NOW,
        NOW + timedelta(hours=1),
        Decimal("60000"),
        Decimal("60100"),
        _lineage(DataSource.BINANCE_SPOT),
    )
    hourly = official_reference_from_binance_hourly_candle(
        hourly_market,
        candle,
        is_price_to_beat=True,
    )
    assert hourly.source == BINANCE_SOURCE
    assert hourly.value == Decimal("60000")

    with pytest.raises(Exception, match="short-horizon"):
        official_reference_from_binance_hourly_candle(short_market, candle, is_price_to_beat=True)


def test_ptb_persistence_and_mid_window_restore(tmp_path) -> None:
    market = _market(Horizon.FIVE_MINUTES)
    twap = ChainlinkTwap(Asset.BTC, Decimal("60000"), 60, NOW, _lineage(DataSource.CHAINLINK_RTDS))
    official = official_reference_from_chainlink_twap(
        market,
        twap,
        recv_ts=NOW + timedelta(milliseconds=20),
        normalized_ts=NOW + timedelta(milliseconds=30),
        is_price_to_beat=True,
    )
    policy = ReferenceFreshnessPolicy(
        timedelta(seconds=2),
        timedelta(seconds=1),
        timedelta(seconds=1),
    )
    identity = PriceToBeatIdentity(
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        market.market_id,
        market.condition_id,
        market.window_start,
        market.settlement_source,
    )
    discovery = MarketDiscovery(
        "event",
        "slug",
        "title",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            market.settlement_source,
            "rules",
            SettlementMethod.CHAINLINK_TWAP,
            "btc-5m-twap-60",
            Asset.BTC,
            Horizon.FIVE_MINUTES,
            60,
            "1",
            _lineage(DataSource.POLYMARKET_GAMMA),
        ),
    )
    record = establish_price_to_beat(
        discovery,
        official,
        observed_at=NOW + timedelta(milliseconds=40),
        persistence_id=identity.persistence_id,
        policy=policy,
    )
    repo = SQLitePriceToBeatRepository(tmp_path / "ptb.sqlite3")
    repo.initialize()
    saved = repo.save_once(identity, record)
    restored = repo.get(identity)
    assert restored == saved
    assert restored is not None and restored.value == Decimal("60000")


def test_external_temporal_state_requires_real_warmup_and_no_future_data() -> None:
    state = ExternalTemporalState(max_age=timedelta(seconds=10), minimum_points=3)
    reference = _proxy("proxy-0", "60000")
    warming = state.build_snapshot(asset=Asset.BTC, current_reference=reference, observed_at=NOW)
    assert warming.reason == "FEATURE_HISTORY_WARMING"

    for index, price in enumerate(("60000", "60010", "60030")):
        ts = NOW + timedelta(seconds=index)
        state.add_reference(_proxy(f"proxy-{index}", price, ts))
    state.add_book(
        CryptoTopOfBook(
            Asset.BTC,
            Decimal("60029"),
            Decimal("4"),
            Decimal("60031"),
            Decimal("6"),
            1,
            _lineage(DataSource.BINANCE_SPOT, NOW + timedelta(seconds=2)),
        )
    )
    state.add_trade(
        CryptoTrade(
            Asset.BTC,
            Decimal("60030"),
            Decimal("1"),
            1,
            False,
            _lineage(DataSource.BINANCE_SPOT, NOW + timedelta(seconds=2)),
        )
    )
    ready = state.build_snapshot(
        asset=Asset.BTC,
        current_reference=_proxy("proxy-x", "60030", NOW + timedelta(seconds=2)),
        observed_at=NOW + timedelta(seconds=2),
    )
    assert ready.reason == "FEATURES_READY"
    assert ready.snapshot is not None

    future = _proxy("future", "60040", NOW + timedelta(seconds=30))
    future_result = state.build_snapshot(asset=Asset.BTC, current_reference=future, observed_at=NOW)
    assert future_result.reason == "FEATURE_SOURCE_IN_FUTURE"


def test_shadow_model_and_corpus_states_do_not_promote_without_evidence(tmp_path) -> None:
    registry = empty_registry()
    state = registry.state_for(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    assert state.readiness.ready is False
    assert ShadowModelState.TRAINING_CORPUS_REQUIRED.value == "TRAINING_CORPUS_REQUIRED"

    repo = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repo.initialize()
    assert repo.count(asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES) == 0
    repo.save_pre_outcome(
        record_id="record-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition",
        observed_at=NOW,
        payload={"ptb_ready": False, "model_state": "TRAINING_CORPUS_REQUIRED"},
    )
    assert repo.count(asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES) == 1
