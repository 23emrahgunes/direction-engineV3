"""Canonical asset/horizon windows, discovery identity, and reference state."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    OfficialReference,
    ProxyReference,
)
from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.market_data.contracts import MarketDiscovery, SettlementMethod
from direction_engine_v3.market_data.errors import (
    CanonicalMarketError,
    DiscoveryMismatchError,
    ReferenceUnavailableError,
)

HORIZON_DURATIONS: Final[Mapping[Horizon, timedelta]] = MappingProxyType({
    Horizon.FIVE_MINUTES: timedelta(minutes=5),
    Horizon.FIFTEEN_MINUTES: timedelta(minutes=15),
    Horizon.ONE_HOUR: timedelta(hours=1),
})

OFFICIAL_TWAP_SOURCES: Final[Mapping[Asset, str]] = MappingProxyType({
    Asset.BTC: "https://data.chain.link/streams/btc-usd-twap-60s-streams",
    Asset.ETH: "https://data.chain.link/streams/eth-usd-twap-60s-streams",
    Asset.SOL: "https://data.chain.link/streams/sol-usd-twap-60s-streams",
    Asset.XRP: "https://data.chain.link/streams/xrp-usd-twap-60s-streams",
})


@dataclass(frozen=True, slots=True)
class MarketBucket:
    """One of the exactly twelve supported asset/horizon buckets."""

    asset: Asset
    horizon: Horizon

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")


SUPPORTED_MARKET_BUCKETS: Final[tuple[MarketBucket, ...]] = tuple(
    MarketBucket(asset, horizon) for asset in Asset for horizon in Horizon
)

OFFICIAL_REFERENCE_SOURCES: Final[Mapping[MarketBucket, str]] = MappingProxyType({
    bucket: (
        OFFICIAL_TWAP_SOURCES[bucket.asset]
        if bucket.horizon is not Horizon.ONE_HOUR
        else f"https://www.binance.com/en/trade/{bucket.asset.value}_USDT"
    )
    for bucket in SUPPORTED_MARKET_BUCKETS
})


@dataclass(frozen=True, slots=True)
class CanonicalWindow:
    """A UTC-aligned market window for one supported bucket."""

    bucket: MarketBucket
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.bucket, MarketBucket):
            raise TypeError("bucket must be a MarketBucket")
        require_utc("start", self.start)
        require_utc("end", self.end)
        duration = HORIZON_DURATIONS[self.bucket.horizon]
        if self.end - self.start != duration:
            raise CanonicalMarketError("window duration does not match horizon")
        duration_seconds = int(duration.total_seconds())
        if self.start.microsecond or int(self.start.timestamp()) % duration_seconds:
            raise CanonicalMarketError("window start is not on its canonical UTC boundary")

    def time_to_expiry(self, observed_at: datetime) -> timedelta:
        """Return TTE only while this exact market window is active."""

        require_utc("observed_at", observed_at)
        if observed_at < self.start:
            raise CanonicalMarketError("cannot calculate TTE before the market window")
        if observed_at >= self.end:
            raise CanonicalMarketError("cannot calculate TTE for an expired market window")
        return self.end - observed_at


def window_containing(bucket: MarketBucket, observed_at: datetime) -> CanonicalWindow:
    """Derive the unique UTC window containing an observation."""

    require_utc("observed_at", observed_at)
    duration = HORIZON_DURATIONS[bucket.horizon]
    duration_seconds = int(duration.total_seconds())
    epoch_seconds = int(observed_at.timestamp())
    start_seconds = epoch_seconds - epoch_seconds % duration_seconds
    start = datetime.fromtimestamp(start_seconds, tz=UTC)
    return CanonicalWindow(bucket=bucket, start=start, end=start + duration)


def epoch_slug(window: CanonicalWindow) -> str:
    """Return the current epoch-slug convention for short-window Gamma events."""

    if window.bucket.horizon is Horizon.ONE_HOUR:
        raise CanonicalMarketError("hourly markets do not use the epoch-slug convention")

    return (
        f"{window.bucket.asset.value.lower()}-updown-"
        f"{window.bucket.horizon.value}-{int(window.start.timestamp())}"
    )


def require_canonical_discovery(
    discovery: MarketDiscovery,
    expected_window: CanonicalWindow,
    *,
    expected_slug: str,
) -> None:
    """Cross-check independent discovery, timing, rules, and identity fields."""

    if not isinstance(discovery, MarketDiscovery):
        raise TypeError("discovery must be a MarketDiscovery")
    require_text("expected_slug", expected_slug)
    market = discovery.market
    settlement = discovery.settlement
    if discovery.slug != expected_slug:
        raise DiscoveryMismatchError("discovery slug does not match requested market")
    if market.asset is not expected_window.bucket.asset:
        raise DiscoveryMismatchError("discovered asset does not match requested bucket")
    if market.horizon is not expected_window.bucket.horizon:
        raise DiscoveryMismatchError("discovered horizon does not match requested bucket")
    if market.window_start != expected_window.start or market.window_end != expected_window.end:
        raise DiscoveryMismatchError("discovered event window does not match requested window")
    expected_source = OFFICIAL_REFERENCE_SOURCES[expected_window.bucket]
    if market.settlement_source != expected_source:
        raise DiscoveryMismatchError("market resolution source is not the canonical TWAP source")
    if market.horizon is Horizon.ONE_HOUR:
        if settlement.method is not SettlementMethod.BINANCE_CANDLE:
            raise DiscoveryMismatchError("hourly market must use Binance candle settlement")
        if settlement.reference_period_seconds != 3_600:
            raise DiscoveryMismatchError("hourly candle period must be 3600 seconds")
        if settlement.configuration_id is not None:
            raise DiscoveryMismatchError("hourly market unexpectedly has a TWAP configuration")
    else:
        if settlement.method is not SettlementMethod.CHAINLINK_TWAP:
            raise DiscoveryMismatchError("short-window market must use Chainlink TWAP")
        if settlement.reference_period_seconds != 60:
            raise DiscoveryMismatchError("market TWAP lookback is not 60 seconds")
        expected_config = f"{market.asset.value.lower()}-{market.horizon.value}-twap-60"
        if settlement.configuration_id != expected_config:
            raise DiscoveryMismatchError("crypto market configuration identity mismatch")


@dataclass(frozen=True, slots=True)
class ReferenceFreshnessPolicy:
    """Freshness and boundary tolerances for authoritative reference data."""

    max_source_age: timedelta
    max_receive_latency: timedelta
    boundary_tolerance: timedelta

    def __post_init__(self) -> None:
        for name, value in (
            ("max_source_age", self.max_source_age),
            ("max_receive_latency", self.max_receive_latency),
            ("boundary_tolerance", self.boundary_tolerance),
        ):
            if value < timedelta(0):
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class PriceToBeatRecord:
    """Persistable authoritative opening value; never synthesized from a proxy."""

    condition_id: str
    reference: OfficialReference
    established_at: datetime
    persistence_id: str

    def __post_init__(self) -> None:
        require_text("condition_id", self.condition_id)
        if not isinstance(self.reference, OfficialReference):
            raise TypeError("reference must be an OfficialReference")
        require_utc("established_at", self.established_at)
        require_text("persistence_id", self.persistence_id)

    @property
    def value(self) -> Decimal:
        return self.reference.value


def establish_price_to_beat(
    discovery: MarketDiscovery,
    reference: OfficialReference,
    *,
    observed_at: datetime,
    persistence_id: str,
    policy: ReferenceFreshnessPolicy,
) -> PriceToBeatRecord:
    """Establish PTB at the opening boundary, failing closed after the grace period."""

    require_utc("observed_at", observed_at)
    _require_official_identity(discovery, reference)
    if not reference.is_price_to_beat:
        raise ReferenceUnavailableError("official reference is not marked as price-to-beat")
    start = discovery.market.window_start
    if abs(reference.effective_ts - start) > policy.boundary_tolerance:
        raise ReferenceUnavailableError("official reference is not effective at window open")
    if observed_at > start + policy.boundary_tolerance:
        raise ReferenceUnavailableError("mid-window PTB reconstruction is forbidden")
    if observed_at < start - policy.boundary_tolerance:
        raise ReferenceUnavailableError("PTB cannot be established before boundary tolerance")
    _require_fresh_timestamps(reference.source_ts, reference.recv_ts, observed_at, policy)
    return PriceToBeatRecord(
        condition_id=discovery.market.condition_id,
        reference=reference,
        established_at=observed_at,
        persistence_id=persistence_id,
    )


def restore_price_to_beat(
    discovery: MarketDiscovery,
    record: PriceToBeatRecord,
    *,
    restored_at: datetime,
    policy: ReferenceFreshnessPolicy,
) -> PriceToBeatRecord:
    """Restore a boundary-proven PTB without treating its fixed value as stale."""

    require_utc("restored_at", restored_at)
    _require_official_identity(discovery, record.reference)
    if record.condition_id != discovery.market.condition_id:
        raise ReferenceUnavailableError("persisted PTB condition identity mismatch")
    start = discovery.market.window_start
    if record.established_at > start + policy.boundary_tolerance:
        raise ReferenceUnavailableError("persisted PTB was established too late")
    if restored_at < start or restored_at >= discovery.market.window_end:
        raise ReferenceUnavailableError("PTB may only be restored during its market window")
    return record


def require_fresh_official_reference(
    discovery: MarketDiscovery,
    reference: OfficialReference,
    *,
    observed_at: datetime,
    policy: ReferenceFreshnessPolicy,
) -> None:
    """Validate current authoritative data without accepting proxy values."""

    _require_official_identity(discovery, reference)
    _require_fresh_timestamps(reference.source_ts, reference.recv_ts, observed_at, policy)


def require_fresh_proxy_reference(
    discovery: MarketDiscovery,
    reference: ProxyReference,
    *,
    observed_at: datetime,
    policy: ReferenceFreshnessPolicy,
) -> None:
    """Validate a proxy independently; this does not satisfy an official requirement."""

    if not isinstance(reference, ProxyReference):
        raise TypeError("reference must be a ProxyReference")
    if reference.market_id != discovery.market.market_id:
        raise ReferenceUnavailableError("proxy market identity mismatch")
    if reference.asset is not discovery.market.asset:
        raise ReferenceUnavailableError("proxy asset identity mismatch")
    _require_fresh_timestamps(reference.source_ts, reference.recv_ts, observed_at, policy)


def _require_official_identity(
    discovery: MarketDiscovery, reference: OfficialReference
) -> None:
    if not isinstance(reference, OfficialReference):
        raise TypeError("reference must be an OfficialReference")
    market = discovery.market
    if reference.market_id != market.market_id:
        raise ReferenceUnavailableError("official reference market identity mismatch")
    if reference.asset is not market.asset:
        raise ReferenceUnavailableError("official reference asset identity mismatch")
    if reference.source != market.settlement_source:
        raise ReferenceUnavailableError("official reference source does not match market rules")


def _require_fresh_timestamps(
    source_ts: datetime,
    recv_ts: datetime,
    observed_at: datetime,
    policy: ReferenceFreshnessPolicy,
) -> None:
    require_utc("observed_at", observed_at)
    if source_ts > recv_ts or recv_ts > observed_at:
        raise ReferenceUnavailableError("reference timestamp order is unverifiable")
    source_age = observed_at - source_ts
    receive_latency = recv_ts - source_ts
    if source_age > policy.max_source_age:
        raise ReferenceUnavailableError("reference source value is stale")
    if receive_latency > policy.max_receive_latency:
        raise ReferenceUnavailableError("reference receive latency exceeds policy")
