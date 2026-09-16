"""Official reference routing for Directional PAPER/SHADOW runtime.

Proxy references are intentionally not accepted here. 5m/15m buckets use the
accepted Chainlink 60s TWAP authority; 1h buckets use the rule-defined Binance
USDT one-hour candle authority.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon, Market, OfficialReference
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data.canonical import (
    OFFICIAL_REFERENCE_SOURCES,
    MarketBucket,
)
from direction_engine_v3.market_data.contracts import ChainlinkTwap, EventLineage
from direction_engine_v3.market_data.errors import ReferenceUnavailableError


@dataclass(frozen=True, slots=True)
class BinanceHourlyCandle:
    """Rule-defined 1h Binance candle official reference, not a proxy quote."""

    asset: Asset
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    close_price: Decimal
    lineage: EventLineage

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        require_utc("open_time", self.open_time)
        require_utc("close_time", self.close_time)
        if self.close_time <= self.open_time:
            raise ValueError("close_time must follow open_time")
        require_decimal("open_price", self.open_price, minimum=Decimal("0"), minimum_exclusive=True)
        require_decimal(
            "close_price", self.close_price, minimum=Decimal("0"), minimum_exclusive=True
        )
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


def official_reference_from_chainlink_twap(
    market: Market,
    twap: ChainlinkTwap,
    *,
    recv_ts: datetime,
    normalized_ts: datetime,
    is_price_to_beat: bool,
) -> OfficialReference:
    """Create a short-horizon official reference from Chainlink 60s TWAP only."""

    require_utc("recv_ts", recv_ts)
    require_utc("normalized_ts", normalized_ts)
    bucket = MarketBucket(market.asset, market.horizon)
    if market.horizon is Horizon.ONE_HOUR:
        raise ReferenceUnavailableError("1h official reference must use Binance candle authority")
    if twap.window_seconds != 60:
        raise ReferenceUnavailableError("short-horizon official reference requires 60s TWAP")
    if twap.asset is not market.asset:
        raise ReferenceUnavailableError("Chainlink TWAP asset does not match market")
    source = OFFICIAL_REFERENCE_SOURCES[bucket]
    if market.settlement_source != source:
        raise ReferenceUnavailableError("market settlement source does not match Chainlink rule")
    return OfficialReference(
        reference_id=f"official:{market.condition_id}:chainlink:{int(twap.publisher_ts.timestamp())}",
        market_id=market.market_id,
        asset=market.asset,
        value=twap.value,
        source=source,
        source_ts=twap.publisher_ts,
        recv_ts=recv_ts,
        effective_ts=twap.publisher_ts,
        is_price_to_beat=is_price_to_beat,
    )


def official_reference_from_binance_hourly_candle(
    market: Market,
    candle: BinanceHourlyCandle,
    *,
    is_price_to_beat: bool,
) -> OfficialReference:
    """Create a 1h official reference from the rule-defined Binance candle."""

    if market.horizon is not Horizon.ONE_HOUR:
        raise ReferenceUnavailableError("short-horizon official reference must use Chainlink TWAP")
    if candle.asset is not market.asset:
        raise ReferenceUnavailableError("Binance candle asset does not match market")
    if candle.open_time != market.window_start or candle.close_time != market.window_end:
        raise ReferenceUnavailableError("Binance candle does not match canonical 1h window")
    source = OFFICIAL_REFERENCE_SOURCES[MarketBucket(market.asset, market.horizon)]
    if market.settlement_source != source:
        raise ReferenceUnavailableError("market settlement source does not match Binance rule")
    value = candle.open_price if is_price_to_beat else candle.close_price
    effective_ts = candle.open_time if is_price_to_beat else candle.close_time
    source_ts = candle.lineage.source_ts or effective_ts
    return OfficialReference(
        reference_id=f"official:{market.condition_id}:binance-candle:{int(effective_ts.timestamp())}",
        market_id=market.market_id,
        asset=market.asset,
        value=value,
        source=source,
        source_ts=source_ts,
        recv_ts=candle.lineage.recv_ts,
        effective_ts=effective_ts,
        is_price_to_beat=is_price_to_beat,
    )


def reject_proxy_as_official(source: object) -> None:
    """Documented guard used by tests and runtime diagnostics."""

    if source.__class__.__name__ == "ProxyReference":
        raise TypeError("ProxyReference cannot be converted to OfficialReference")
    require_text("source_type", source.__class__.__name__)
