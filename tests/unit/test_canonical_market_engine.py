from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OutcomeSide,
    ProxyReference,
)
from direction_engine_v3.market_data import (
    HORIZON_DURATIONS,
    OFFICIAL_REFERENCE_SOURCES,
    SUPPORTED_MARKET_BUCKETS,
    CanonicalMarketError,
    CanonicalWindow,
    DataSource,
    DiscoveryMismatchError,
    EventLineage,
    MarketBucket,
    MarketDiscovery,
    ReferenceFreshnessPolicy,
    ReferenceUnavailableError,
    SettlementMetadata,
    SettlementMethod,
    epoch_slug,
    establish_price_to_beat,
    require_canonical_discovery,
    require_fresh_official_reference,
    require_fresh_proxy_reference,
    restore_price_to_beat,
    window_containing,
)

START = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
POLICY = ReferenceFreshnessPolicy(
    max_source_age=timedelta(seconds=2),
    max_receive_latency=timedelta(seconds=1),
    boundary_tolerance=timedelta(seconds=1),
)


def make_discovery(
    *,
    asset: Asset = Asset.BTC,
    horizon: Horizon = Horizon.FIVE_MINUTES,
) -> tuple[MarketDiscovery, CanonicalWindow]:
    bucket = MarketBucket(asset, horizon)
    window = CanonicalWindow(bucket, START, START + HORIZON_DURATIONS[horizon])
    source = OFFICIAL_REFERENCE_SOURCES[bucket]
    market = Market(
        market_id=f"{asset.value.lower()}-{horizon.value}-market",
        condition_id=f"{asset.value.lower()}-{horizon.value}-condition",
        asset=asset,
        horizon=horizon,
        tokens=(
            MarketToken("up-token", OutcomeSide.UP),
            MarketToken("down-token", OutcomeSide.DOWN),
        ),
        window_start=window.start,
        window_end=window.end,
        settlement_source=source,
    )
    settlement = SettlementMetadata(
        market_id=market.market_id,
        condition_id=market.condition_id,
        resolution_source=source,
        rules="Chainlink TWAP opening-to-closing comparison.",
        method=(
            SettlementMethod.BINANCE_CANDLE
            if horizon is Horizon.ONE_HOUR
            else SettlementMethod.CHAINLINK_TWAP
        ),
        configuration_id=(
            None
            if horizon is Horizon.ONE_HOUR
            else f"{asset.value.lower()}-{horizon.value}-twap-60"
        ),
        asset=asset,
        horizon=horizon,
        reference_period_seconds=3_600 if horizon is Horizon.ONE_HOUR else 60,
        version="v1",
        lineage=EventLineage(
            source=DataSource.POLYMARKET_GAMMA,
            source_ts=START - timedelta(seconds=2),
            recv_ts=START - timedelta(seconds=1),
            normalized_ts=START - timedelta(seconds=1),
            recv_monotonic_ns=1,
        ),
    )
    return (
        MarketDiscovery(
            event_id="event-1",
            slug=(
                f"{asset.value.lower()}-hourly-source-slug"
                if horizon is Horizon.ONE_HOUR
                else epoch_slug(window)
            ),
            question=f"{asset.value} Up or Down",
            market=market,
            settlement=settlement,
        ),
        window,
    )


def make_official(
    discovery: MarketDiscovery,
    *,
    source_ts: datetime = START,
    recv_ts: datetime = START + timedelta(milliseconds=100),
    effective_ts: datetime = START,
) -> OfficialReference:
    market = discovery.market
    return OfficialReference(
        reference_id="official-opening-1",
        market_id=market.market_id,
        asset=market.asset,
        value=Decimal("60000.125"),
        source=market.settlement_source,
        source_ts=source_ts,
        recv_ts=recv_ts,
        effective_ts=effective_ts,
        is_price_to_beat=True,
    )


def test_supported_market_buckets_are_exactly_the_twelve_required_pairs() -> None:
    assert len(SUPPORTED_MARKET_BUCKETS) == 12
    assert {(bucket.asset, bucket.horizon) for bucket in SUPPORTED_MARKET_BUCKETS} == {
        (asset, horizon) for asset in Asset for horizon in Horizon
    }


@pytest.mark.parametrize("bucket", SUPPORTED_MARKET_BUCKETS)
def test_window_containing_is_aligned_and_exact_for_every_bucket(bucket: MarketBucket) -> None:
    observed = START + timedelta(minutes=37, seconds=41, microseconds=123)
    window = window_containing(bucket, observed)
    duration = HORIZON_DURATIONS[bucket.horizon]
    assert window.end - window.start == duration
    assert int(window.start.timestamp()) % int(duration.total_seconds()) == 0
    assert window.start <= observed < window.end


def test_tte_comes_from_the_selected_window_and_rejects_wrong_time_context() -> None:
    _, window = make_discovery()
    assert window.time_to_expiry(START + timedelta(seconds=90)) == timedelta(seconds=210)
    with pytest.raises(CanonicalMarketError, match="before"):
        window.time_to_expiry(START - timedelta(microseconds=1))
    with pytest.raises(CanonicalMarketError, match="expired"):
        window.time_to_expiry(window.end)


def test_epoch_slug_is_limited_to_current_short_window_convention() -> None:
    _, short_window = make_discovery()
    assert epoch_slug(short_window) == "btc-updown-5m-1789473600"
    _, hourly_window = make_discovery(horizon=Horizon.ONE_HOUR)
    with pytest.raises(CanonicalMarketError, match="hourly"):
        epoch_slug(hourly_window)


def test_domain_market_rejects_wrong_duration_and_misaligned_start() -> None:
    discovery, _ = make_discovery()
    market = discovery.market
    with pytest.raises(ValueError, match="duration"):
        replace(market, window_end=market.window_end + timedelta(minutes=1))
    with pytest.raises(ValueError, match="canonical UTC boundary"):
        replace(
            market,
            window_start=market.window_start + timedelta(minutes=1),
            window_end=market.window_end + timedelta(minutes=1),
        )


@pytest.mark.parametrize("bucket", SUPPORTED_MARKET_BUCKETS)
def test_canonical_discovery_accepts_all_and_only_expected_buckets(bucket: MarketBucket) -> None:
    discovery, window = make_discovery(asset=bucket.asset, horizon=bucket.horizon)
    require_canonical_discovery(discovery, window, expected_slug=discovery.slug)


def test_canonical_discovery_rejects_slug_window_source_and_config_mismatches() -> None:
    discovery, window = make_discovery()
    with pytest.raises(DiscoveryMismatchError, match="slug"):
        require_canonical_discovery(discovery, window, expected_slug="wrong-slug")

    next_window = CanonicalWindow(
        window.bucket,
        window.end,
        window.end + HORIZON_DURATIONS[window.bucket.horizon],
    )
    with pytest.raises(DiscoveryMismatchError, match="window"):
        require_canonical_discovery(
            discovery, next_window, expected_slug=discovery.slug
        )

    wrong_source = "https://example.invalid/not-authoritative"
    wrong_market = replace(discovery.market, settlement_source=wrong_source)
    wrong_settlement = replace(discovery.settlement, resolution_source=wrong_source)
    with pytest.raises(DiscoveryMismatchError, match="canonical TWAP"):
        require_canonical_discovery(
            replace(discovery, market=wrong_market, settlement=wrong_settlement),
            window,
            expected_slug=discovery.slug,
        )

    wrong_config = replace(discovery.settlement, configuration_id="btc-15m-twap-60")
    with pytest.raises(DiscoveryMismatchError, match="configuration identity"):
        require_canonical_discovery(
            replace(discovery, settlement=wrong_config),
            window,
            expected_slug=discovery.slug,
        )


def test_price_to_beat_is_established_only_from_fresh_official_boundary_data() -> None:
    discovery, _ = make_discovery()
    official = make_official(discovery)
    record = establish_price_to_beat(
        discovery,
        official,
        observed_at=START + timedelta(milliseconds=200),
        persistence_id="ptb-row-1",
        policy=POLICY,
    )
    assert record.value == Decimal("60000.125")
    assert type(record.reference) is OfficialReference


def test_proxy_cannot_be_used_to_establish_official_price_to_beat() -> None:
    discovery, _ = make_discovery()
    official = make_official(discovery)
    proxy = ProxyReference(
        reference_id="binance-1",
        market_id=official.market_id,
        asset=official.asset,
        value=official.value,
        source="binance-spot",
        source_ts=official.source_ts,
        recv_ts=official.recv_ts,
        effective_ts=official.effective_ts,
    )
    with pytest.raises(TypeError, match="OfficialReference"):
        establish_price_to_beat(
            discovery,
            proxy,  # type: ignore[arg-type]
            observed_at=START + timedelta(milliseconds=200),
            persistence_id="ptb-row-1",
            policy=POLICY,
        )


def test_mid_window_or_stale_official_data_cannot_reconstruct_ptb() -> None:
    discovery, _ = make_discovery()
    official = make_official(discovery)
    with pytest.raises(ReferenceUnavailableError, match="mid-window"):
        establish_price_to_beat(
            discovery,
            official,
            observed_at=START + timedelta(minutes=1),
            persistence_id="late",
            policy=POLICY,
        )

    stale = make_official(
        discovery,
        source_ts=START - timedelta(seconds=5),
        recv_ts=START - timedelta(seconds=4),
    )
    with pytest.raises(ReferenceUnavailableError, match="stale"):
        establish_price_to_beat(
            discovery,
            stale,
            observed_at=START,
            persistence_id="stale",
            policy=POLICY,
        )


def test_mid_window_restart_requires_the_persisted_boundary_record() -> None:
    discovery, _ = make_discovery()
    record = establish_price_to_beat(
        discovery,
        make_official(discovery),
        observed_at=START + timedelta(milliseconds=200),
        persistence_id="ptb-row-1",
        policy=POLICY,
    )
    assert (
        restore_price_to_beat(
            discovery,
            record,
            restored_at=START + timedelta(minutes=3),
            policy=POLICY,
        )
        is record
    )
    with pytest.raises(ReferenceUnavailableError, match="condition"):
        restore_price_to_beat(
            discovery,
            replace(record, condition_id="other-condition"),
            restored_at=START + timedelta(minutes=3),
            policy=POLICY,
        )


def test_current_official_and_proxy_freshness_paths_remain_separate() -> None:
    discovery, _ = make_discovery()
    official = make_official(discovery)
    proxy = ProxyReference(
        reference_id="proxy-1",
        market_id=discovery.market.market_id,
        asset=discovery.market.asset,
        value=Decimal("60001"),
        source="binance-spot",
        source_ts=START,
        recv_ts=START + timedelta(milliseconds=50),
        effective_ts=START,
    )
    observed = START + timedelta(milliseconds=200)
    require_fresh_official_reference(
        discovery, official, observed_at=observed, policy=POLICY
    )
    require_fresh_proxy_reference(discovery, proxy, observed_at=observed, policy=POLICY)
    with pytest.raises(TypeError, match="OfficialReference"):
        require_fresh_official_reference(
            discovery,
            proxy,  # type: ignore[arg-type]
            observed_at=observed,
            policy=POLICY,
        )
    with pytest.raises(TypeError, match="ProxyReference"):
        require_fresh_proxy_reference(
            discovery,
            official,  # type: ignore[arg-type]
            observed_at=observed,
            policy=POLICY,
        )
