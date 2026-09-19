import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon, OfficialReference, ProxyReference
from direction_engine_v3.features import ExternalTemporalState
from direction_engine_v3.market_data import (
    CryptoTopOfBook,
    CryptoTrade,
    DataSource,
    EventLineage,
    MarketBucket,
    PriceToBeatRecord,
)
from direction_engine_v3.market_data.official_runtime import PriceToBeatResolution
from direction_engine_v3.shadow.daemon import (
    PublicShadowDataClient,
    ShadowDaemon,
    new_evidence_window,
)
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
EVALUATION_AT = NOW + timedelta(milliseconds=3100)


class SequenceClock:
    def __init__(self) -> None:
        self._index = 0
        self.returned: list[datetime] = []

    def utc_now(self) -> datetime:
        value = NOW + timedelta(milliseconds=100 * self._index)
        self._index += 1
        self.returned.append(value)
        return value

    def monotonic_ns(self) -> int:
        return self._index


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def utc_now(self) -> datetime:
        return self._value

    def monotonic_ns(self) -> int:
        return 1


class FixedOfficial:
    def __init__(self) -> None:
        self.observed_at: datetime | None = None

    async def resolve(self, discovery, *, observed_at):
        self.observed_at = observed_at
        official = OfficialReference(
            "official",
            discovery.market.market_id,
            discovery.market.asset,
            Decimal("60000"),
            discovery.market.settlement_source,
            NOW,
            observed_at,
            discovery.market.window_start,
            True,
        )
        return PriceToBeatResolution(
            PriceToBeatRecord(
                discovery.market.condition_id,
                official,
                observed_at,
                "ptb:test",
            ),
            official,
            "PTB_READY",
            "PTB_ESTABLISHED",
        )

    def chainlink_status(self):
        return _Status()

    def binance_hourly_status(self):
        return _Status()


class _Status:
    def as_dict(self) -> dict[str, object]:
        return {"status": "READY"}


class FixtureTransport:
    async def get_json(self, url: str, *, params=None):
        params = params or {}
        if "gamma-api" in url:
            return {"id": "event-1", "markets": [_gamma_market()]}
        if "fee-rate" in url:
            return {"base_fee": 0}
        if "book" in url:
            token_id = str(params["token_id"])
            return {
                "market": "condition-1",
                "asset_id": token_id,
                "timestamp": str(int((NOW + timedelta(milliseconds=800)).timestamp() * 1000)),
                "hash": f"hash-{token_id}",
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.41", "size": "10"}],
                "min_order_size": "1",
                "tick_size": "0.01",
            }
        if "depth" in url:
            return {
                "lastUpdateId": 123,
                "bids": [["60010", "2"]],
                "asks": [["60012", "2"]],
            }
        if "aggTrades" in url:
            return [
                {
                    "a": 1,
                    "p": "60011",
                    "q": "1",
                    "T": int((NOW + timedelta(milliseconds=1200)).timestamp() * 1000),
                    "m": False,
                }
            ]
        raise AssertionError(f"unexpected URL {url}")


class MarketOverrideTransport(FixtureTransport):
    def __init__(self, **overrides: object) -> None:
        self._overrides = overrides

    async def get_json(self, url: str, *, params=None):
        if "gamma-api" in url:
            return {"id": "event-1", "markets": [_gamma_market() | self._overrides]}
        return await super().get_json(url, params=params)


def test_collect_bucket_uses_post_collection_evaluation_clock_for_features_and_pricing(
    tmp_path,
) -> None:
    clock = SequenceClock()
    feature_state = ExternalTemporalState(max_age=timedelta(seconds=30), minimum_points=2)
    _seed_feature_history(feature_state)
    official = FixedOfficial()
    client = PublicShadowDataClient(
        FixtureTransport(),
        clock,
        official_ptb=official,
        feature_state=feature_state,
    )

    state = asyncio.run(
        client.collect_bucket(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES), now=NOW)
    )

    assert official.observed_at is not None
    assert official.observed_at < state.observed_at
    assert state.observed_at == EVALUATION_AT
    assert state.feature_status == "FEATURES_READY"
    assert state.proxy_reference is not None
    assert state.up_book is not None
    assert state.down_book is not None
    assert state.up_fee_schedule is not None
    assert state.down_fee_schedule is not None
    assert state.proxy_reference.source_ts <= state.observed_at
    assert state.up_book.lineage.recv_ts <= state.observed_at
    assert state.down_book.lineage.recv_ts <= state.observed_at
    assert state.up_fee_schedule.lineage.recv_ts <= state.observed_at
    assert state.down_fee_schedule.lineage.recv_ts <= state.observed_at
    assert "FEATURE_SOURCE_IN_FUTURE" not in {
        stage.reason for stage in state.pipeline_stages
    }

    daemon = _daemon(tmp_path)
    _up, _down, pricing_status = daemon._directional_pricing(state)
    assert pricing_status == "EXECUTABLE_PRICE_READY"


def test_collect_bucket_marks_market_expired_at_exact_window_end_without_feature_crash() -> None:
    state = asyncio.run(
        PublicShadowDataClient(
            MarketOverrideTransport(endDate=(NOW + timedelta(minutes=5)).isoformat()),
            FixedClock(NOW + timedelta(minutes=5)),
            official_ptb=FixedOfficial(),
            feature_state=ExternalTemporalState(max_age=timedelta(seconds=30), minimum_points=2),
        ).collect_bucket(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES), now=NOW)
    )

    assert state.discovery is not None
    assert state.observed_at == NOW + timedelta(minutes=5)
    assert state.unavailable_reason == "MARKET_WINDOW_EXPIRED"
    assert state.feature_status == "MARKET_WINDOW_EXPIRED"
    assert state.directional_features is None
    assert any(
        stage.stage == "FEATURE_BUILD"
        and stage.status == "SKIPPED"
        and stage.reason == "MARKET_WINDOW_EXPIRED"
        for stage in state.pipeline_stages
    )


def test_collect_bucket_marks_market_not_started_without_feature_crash() -> None:
    future_start = NOW + timedelta(minutes=5)
    state = asyncio.run(
        PublicShadowDataClient(
            MarketOverrideTransport(
                eventStartTime=future_start.isoformat(),
                startDate=future_start.isoformat(),
                endDate=(future_start + timedelta(minutes=5)).isoformat(),
            ),
            SequenceClock(),
            official_ptb=FixedOfficial(),
            feature_state=ExternalTemporalState(max_age=timedelta(seconds=30), minimum_points=2),
        ).collect_bucket(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES), now=NOW)
    )

    assert state.discovery is not None
    assert state.unavailable_reason == "MARKET_WINDOW_NOT_STARTED"
    assert state.feature_status == "MARKET_WINDOW_NOT_STARTED"
    assert state.directional_features is None


def test_future_validators_still_reject_genuinely_future_feature_and_pricing(
    tmp_path,
) -> None:
    state = asyncio.run(
        PublicShadowDataClient(
            FixtureTransport(),
            SequenceClock(),
            official_ptb=FixedOfficial(),
            feature_state=ExternalTemporalState(max_age=timedelta(seconds=30), minimum_points=2),
        ).collect_bucket(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES), now=NOW)
    )
    assert state.proxy_reference is not None
    assert state.discovery is not None
    feature_state = ExternalTemporalState(max_age=timedelta(seconds=30), minimum_points=2)
    feature_state.add_reference(
        ProxyReference(
            "future",
            state.discovery.market.market_id,
            Asset.BTC,
            Decimal("60000"),
            "BINANCE_PUBLIC_DEPTH",
            state.observed_at + timedelta(seconds=1),
            state.observed_at + timedelta(seconds=1),
            state.observed_at + timedelta(seconds=1),
        )
    )
    result = feature_state.build_snapshot(
        asset=Asset.BTC,
        current_reference=state.proxy_reference,
        observed_at=state.observed_at - timedelta(seconds=1),
    )
    assert result.reason == "FEATURE_SOURCE_IN_FUTURE"

    stale_clock_state = replace(state, observed_at=NOW)
    _up, _down, pricing_status = _daemon(tmp_path)._directional_pricing(stale_clock_state)
    assert pricing_status.startswith("EXECUTABLE_PRICE_UNAVAILABLE:")
    assert "future" in pricing_status


def _seed_feature_history(feature_state: ExternalTemporalState) -> None:
    lineage = EventLineage(
        DataSource.BINANCE_SPOT,
        NOW - timedelta(seconds=1),
        NOW - timedelta(seconds=1),
        NOW - timedelta(seconds=1),
        1,
    )
    feature_state.add_reference(
        ProxyReference(
            "seed",
            "market-1",
            Asset.BTC,
            Decimal("60000"),
            "BINANCE_PUBLIC_DEPTH",
            NOW - timedelta(seconds=1),
            NOW - timedelta(seconds=1),
            NOW - timedelta(seconds=1),
        )
    )
    feature_state.add_book(
        CryptoTopOfBook(
            Asset.BTC,
            Decimal("59999"),
            Decimal("2"),
            Decimal("60001"),
            Decimal("2"),
            1,
            lineage,
        )
    )
    feature_state.add_trade(
        CryptoTrade(
            Asset.BTC,
            Decimal("60000"),
            Decimal("1"),
            1,
            False,
            lineage,
        )
    )


def _daemon(tmp_path) -> ShadowDaemon:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    return ShadowDaemon(
        data_client=PublicShadowDataClient(FixtureTransport(), SequenceClock()),
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
    )


def _gamma_market() -> dict[str, object]:
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "question": "Bitcoin Up or Down",
        "slug": "btc-updown-5m",
        "outcomes": '["Down", "Up"]',
        "clobTokenIds": '["down-token", "up-token"]',
        "startDate": "2026-09-17T11:55:00Z",
        "eventStartTime": "2026-09-17T12:00:00Z",
        "endDate": "2026-09-17T12:05:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": "Resolve from Chainlink TWAP.",
        "cryptoMarketConfigId": "btc-5m-twap-60",
        "cryptoMarketConfig": {
            "id": "btc-5m-twap-60",
            "asset": "btc",
            "duration": "5m",
            "twapEnabled": True,
            "twapLookbackSeconds": 60,
        },
        "version": "v1",
        "updatedAt": "2026-09-17T11:59:59Z",
    }
