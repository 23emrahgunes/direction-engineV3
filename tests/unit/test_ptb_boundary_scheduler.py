import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OutcomeSide,
)
from direction_engine_v3.market_data import (
    HORIZON_DURATIONS,
    OFFICIAL_REFERENCE_SOURCES,
    SUPPORTED_MARKET_BUCKETS,
    DataSource,
    EventLineage,
    MarketBucket,
    MarketDataError,
    MarketDiscovery,
    PriceToBeatRecord,
    ReferenceFreshnessPolicy,
    SettlementMetadata,
    SettlementMethod,
)
from direction_engine_v3.market_data.official_runtime import PriceToBeatResolution
from direction_engine_v3.shadow.ptb_scheduler import (
    PTBBoundaryScheduler,
    PTBBoundarySchedulerConfig,
)
from direction_engine_v3.shadow.storage import SQLiteShadowRepository

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
POLICY = ReferenceFreshnessPolicy(
    max_source_age=timedelta(seconds=120),
    max_receive_latency=timedelta(seconds=120),
    boundary_tolerance=timedelta(seconds=90),
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def utc_now(self) -> datetime:
        return self.now

    def monotonic_ns(self) -> int:
        return int(self.now.timestamp() * 1_000_000_000)


class FakeDiscoveryClient:
    def __init__(self, *, fail_assets: set[Asset] | None = None) -> None:
        self.fail_assets = fail_assets or set()
        self.calls: list[tuple[MarketBucket, datetime]] = []

    async def discover_market(
        self, bucket: MarketBucket, *, window_start: datetime
    ) -> MarketDiscovery:
        self.calls.append((bucket, window_start))
        if bucket.asset in self.fail_assets:
            raise MarketDataError("temporary gamma miss")
        return _discovery(bucket, window_start)


class SequencedDiscoveryClient(FakeDiscoveryClient):
    def __init__(self, *, failures_before_success: int) -> None:
        super().__init__()
        self.failures_before_success = failures_before_success

    async def discover_market(
        self, bucket: MarketBucket, *, window_start: datetime
    ) -> MarketDiscovery:
        self.calls.append((bucket, window_start))
        if len(self.calls) <= self.failures_before_success:
            raise MarketDataError("temporary gamma miss")
        return _discovery(bucket, window_start)


class FakePTBService:
    def __init__(self) -> None:
        self.calls: list[MarketDiscovery] = []

    async def resolve(
        self, discovery: MarketDiscovery, *, observed_at: datetime
    ) -> PriceToBeatResolution:
        self.calls.append(discovery)
        ptb = _ptb(discovery, observed_at)
        return PriceToBeatResolution(
            ptb,
            ptb.reference,
            "PTB_READY",
            "PTB_ESTABLISHED",
            restored=False,
        )


class PendingPTBService:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(
        self, discovery: MarketDiscovery, *, observed_at: datetime
    ) -> PriceToBeatResolution:
        self.calls += 1
        return PriceToBeatResolution(
            None,
            None,
            "PTB_UNAVAILABLE",
            "BOUNDARY_HISTORY_MISSING",
        )


def test_scheduler_detects_all_hourly_overlap_buckets(tmp_path: Path) -> None:
    asyncio.run(_assert_scheduler_detects_all_hourly_overlap_buckets(tmp_path))


async def _assert_scheduler_detects_all_hourly_overlap_buckets(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=2))
    discovery = FakeDiscoveryClient()
    scheduler = _scheduler(shadow, clock, discovery, FakePTBService())

    await scheduler.run_due_once()
    await scheduler.wait_idle()

    events = shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20)
    assert len(events) == 12
    assert len(discovery.calls) == len(SUPPORTED_MARKET_BUCKETS)
    assert {event["payload"]["scheduler_outcome"] for event in events} == {"PTB_READY"}
    assert {event["payload"]["ptb_status"] for event in events} == {"PTB_READY"}


def test_one_asset_failure_does_not_block_other_assets(tmp_path: Path) -> None:
    asyncio.run(_assert_one_asset_failure_does_not_block_other_assets(tmp_path))


async def _assert_one_asset_failure_does_not_block_other_assets(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=89, milliseconds=500))
    discovery = FakeDiscoveryClient(fail_assets={Asset.BTC})
    scheduler = _scheduler(
        shadow,
        clock,
        discovery,
        FakePTBService(),
        config=PTBBoundarySchedulerConfig(retry_interval_seconds=1.0),
    )

    await scheduler.run_due_once()
    await scheduler.wait_idle()

    events = shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20)
    payloads = [event["payload"] for event in events]
    ready_assets = {
        payload["asset"] for payload in payloads if payload["ptb_status"] == "PTB_READY"
    }
    exhausted_assets = {
        payload["asset"]
        for payload in payloads
        if payload["scheduler_outcome"] == "ATTEMPTS_EXHAUSTED"
    }
    assert {"ETH", "SOL", "XRP"}.issubset(ready_assets)
    assert exhausted_assets == {"BTC"}


def test_startup_after_boundary_tolerance_records_missed(tmp_path: Path) -> None:
    asyncio.run(_assert_startup_after_boundary_tolerance_records_missed(tmp_path))


async def _assert_startup_after_boundary_tolerance_records_missed(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=91))
    discovery = FakeDiscoveryClient()
    scheduler = _scheduler(shadow, clock, discovery, FakePTBService())

    await scheduler.run_due_once()

    events = shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20)
    assert len(events) == 12
    assert not discovery.calls
    assert {event["payload"]["scheduler_outcome"] for event in events} == {"MISSED_BOUNDARY"}
    assert {event["payload"]["ptb_reason"] for event in events} == {"MISSED_BOUNDARY"}


def test_transient_discovery_failure_retries_then_succeeds(tmp_path: Path) -> None:
    asyncio.run(_assert_transient_discovery_failure_retries_then_succeeds(tmp_path))


async def _assert_transient_discovery_failure_retries_then_succeeds(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=2))
    discovery = SequencedDiscoveryClient(failures_before_success=1)
    scheduler = _scheduler(
        shadow,
        clock,
        discovery,
        FakePTBService(),
        config=PTBBoundarySchedulerConfig(
            retry_interval_seconds=0.01,
            max_concurrency=1,
        ),
    )

    await scheduler.run_due_once()
    await scheduler.wait_idle()

    events = shadow.latest_events(
        event_type="PTB_BOUNDARY_SCHEDULER",
        bucket_key="BTC-5m",
        limit=1,
    )
    assert events[0]["payload"]["scheduler_outcome"] == "PTB_READY"
    assert events[0]["payload"]["attempt_count"] == 2


def test_deadline_exhaustion_does_not_fabricate_ptb(tmp_path: Path) -> None:
    asyncio.run(_assert_deadline_exhaustion_does_not_fabricate_ptb(tmp_path))


async def _assert_deadline_exhaustion_does_not_fabricate_ptb(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=89, milliseconds=500))
    scheduler = _scheduler(
        shadow,
        clock,
        FakeDiscoveryClient(),
        PendingPTBService(),
        config=PTBBoundarySchedulerConfig(retry_interval_seconds=1.0),
    )

    await scheduler.run_due_once()
    await scheduler.wait_idle()

    events = shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20)
    assert len(events) == 12
    assert {event["payload"]["scheduler_outcome"] for event in events} == {
        "ATTEMPTS_EXHAUSTED"
    }
    assert {event["payload"]["ptb_value"] for event in events} == {None}


def test_scheduler_event_is_idempotent_for_same_bucket_window(tmp_path: Path) -> None:
    asyncio.run(_assert_scheduler_event_is_idempotent_for_same_bucket_window(tmp_path))


async def _assert_scheduler_event_is_idempotent_for_same_bucket_window(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=2))
    scheduler = _scheduler(shadow, clock, FakeDiscoveryClient(), FakePTBService())

    await scheduler.run_due_once()
    await scheduler.wait_idle()
    await scheduler.run_due_once()

    events = shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20)
    assert len(events) == 12


def test_scheduler_shutdown_cancels_workers_cleanly(tmp_path: Path) -> None:
    asyncio.run(_assert_scheduler_shutdown_cancels_workers_cleanly(tmp_path))


async def _assert_scheduler_shutdown_cancels_workers_cleanly(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    clock = MutableClock(NOW + timedelta(seconds=2))
    scheduler = _scheduler(
        shadow,
        clock,
        FakeDiscoveryClient(),
        PendingPTBService(),
        config=PTBBoundarySchedulerConfig(retry_interval_seconds=30.0),
    )

    await scheduler.run_due_once()
    await scheduler.cancel()
    await scheduler.run_due_once()

    assert shadow.latest_events(event_type="PTB_BOUNDARY_SCHEDULER", limit=20) == ()


def _scheduler(
    shadow: SQLiteShadowRepository,
    clock: MutableClock,
    discovery: object,
    ptb_service: object,
    *,
    config: PTBBoundarySchedulerConfig | None = None,
) -> PTBBoundaryScheduler:
    return PTBBoundaryScheduler(
        discovery_client=discovery,
        official_ptb=ptb_service,  # type: ignore[arg-type]
        shadow_repository=shadow,
        evidence_window_id="window",
        clock=clock,
        policy=POLICY,
        config=config or PTBBoundarySchedulerConfig(tick_seconds=0.01),
        sleep=asyncio.sleep,
    )


def _shadow(tmp_path: Path) -> SQLiteShadowRepository:
    shadow = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    shadow.initialize()
    return shadow


def _discovery(bucket: MarketBucket, window_start: datetime) -> MarketDiscovery:
    duration = HORIZON_DURATIONS[bucket.horizon]
    source = OFFICIAL_REFERENCE_SOURCES[bucket]
    market = Market(
        f"market-{bucket.asset.value}-{bucket.horizon.value}",
        f"condition-{bucket.asset.value}-{bucket.horizon.value}",
        bucket.asset,
        bucket.horizon,
        (
            MarketToken(f"{bucket.asset.value}-up", OutcomeSide.UP),
            MarketToken(f"{bucket.asset.value}-down", OutcomeSide.DOWN),
        ),
        window_start,
        window_start + duration,
        source,
    )
    method = (
        SettlementMethod.BINANCE_CANDLE
        if bucket.horizon is Horizon.ONE_HOUR
        else SettlementMethod.CHAINLINK_TWAP
    )
    return MarketDiscovery(
        f"event-{bucket.asset.value}-{bucket.horizon.value}",
        f"slug-{bucket.asset.value}-{bucket.horizon.value}",
        "question",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            source,
            "rules",
            method,
            None if bucket.horizon is Horizon.ONE_HOUR else "chainlink-60s",
            bucket.asset,
            bucket.horizon,
            3600 if bucket.horizon is Horizon.ONE_HOUR else 60,
            "1",
            _lineage(DataSource.POLYMARKET_GAMMA, window_start),
        ),
    )


def _ptb(discovery: MarketDiscovery, observed_at: datetime) -> PriceToBeatRecord:
    reference = OfficialReference(
        f"official-{discovery.market.condition_id}",
        discovery.market.market_id,
        discovery.market.asset,
        Decimal("100"),
        discovery.market.settlement_source,
        discovery.market.window_start,
        observed_at,
        discovery.market.window_start,
        True,
    )
    return PriceToBeatRecord(
        discovery.market.condition_id,
        reference,
        observed_at,
        f"ptb:{discovery.market.condition_id}",
    )


def _lineage(source: DataSource, now: datetime) -> EventLineage:
    return EventLineage(source, now, now, now, 1)
