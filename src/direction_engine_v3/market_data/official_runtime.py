"""Runtime official-reference collectors and PTB boundary resolution."""

import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.adapters.chainlink.rtds import (
    RTDS_HEARTBEAT_SECONDS,
    RTDS_WS_URL,
    parse_twap,
    twap_subscription,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon, OfficialReference
from direction_engine_v3.domain._validation import require_utc
from direction_engine_v3.market_data.canonical import (
    PriceToBeatRecord,
    ReferenceFreshnessPolicy,
    establish_price_to_beat,
    restore_price_to_beat,
)
from direction_engine_v3.market_data.clock import SystemClock, utc_from_milliseconds
from direction_engine_v3.market_data.contracts import (
    ChainlinkTwap,
    DataSource,
    EventLineage,
    MarketDiscovery,
)
from direction_engine_v3.market_data.errors import MarketDataSchemaError, ReferenceUnavailableError
from direction_engine_v3.market_data.official import (
    BinanceHourlyCandle,
    official_reference_from_binance_hourly_candle,
    official_reference_from_chainlink_twap,
)
from direction_engine_v3.market_data.ptb_repository import (
    PriceToBeatIdentity,
    SQLitePriceToBeatRepository,
)
from direction_engine_v3.market_data.retry import RetryPolicy

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


@dataclass(frozen=True, slots=True)
class ChainlinkCollectorStatus:
    connection: str
    last_message_at: datetime | None
    reconnect_count: int
    message_count: int
    history_size_by_asset: Mapping[str, int]
    latest_twap_by_asset: Mapping[str, str]
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "connection": self.connection,
            "last_message_at": self.last_message_at.isoformat()
            if self.last_message_at is not None
            else None,
            "reconnect_count": self.reconnect_count,
            "message_count": self.message_count,
            "history_size_by_asset": dict(self.history_size_by_asset),
            "latest_twap_by_asset": dict(self.latest_twap_by_asset),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class BinanceHourlyCollectorStatus:
    last_request_at: datetime | None
    last_candle_by_asset: Mapping[str, str]
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "last_request_at": self.last_request_at.isoformat()
            if self.last_request_at is not None
            else None,
            "last_candle_by_asset": dict(self.last_candle_by_asset),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class PriceToBeatResolution:
    price_to_beat: PriceToBeatRecord | None
    official_reference: OfficialReference | None
    ptb_status: str
    reason: str
    restored: bool = False

    @property
    def ready(self) -> bool:
        return self.price_to_beat is not None


class ChainlinkTwapCollector:
    """Persistent RTDS 60s-TWAP collector with bounded per-asset history."""

    def __init__(
        self,
        transport: PublicTransport,
        clock: SystemClock,
        *,
        assets: Sequence[Asset] = tuple(Asset),
        history_limit: int = 4096,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self._transport = transport
        self._clock = clock
        self._assets = tuple(assets)
        self._history: dict[Asset, deque[ChainlinkTwap]] = {
            asset: deque(maxlen=history_limit) for asset in self._assets
        }
        self._latest: dict[Asset, ChainlinkTwap] = {}
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=8)
        self._connected = False
        self._reconnect_count = 0
        self._message_count = 0
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until stopped; retry transport errors with bounded backoff."""

        while not stop_event.is_set():
            try:
                self._connected = True
                subscription = _combined_twap_subscription(self._assets)
                async for raw in self._transport.resilient_websocket_json(
                    RTDS_WS_URL,
                    subscription=subscription,
                    text_heartbeat_seconds=RTDS_HEARTBEAT_SECONDS,
                    retry_policy=self._retry_policy,
                ):
                    if stop_event.is_set():
                        break
                    self.handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reconnect_count += 1
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                except TimeoutError:
                    continue
            finally:
                self._connected = False

    def handle_message(self, raw: object) -> int:
        """Parse one RTDS message and record every valid supported 60s TWAP."""

        parsed = 0
        for payload in _iter_rtds_payloads(raw):
            for asset in self._assets:
                recv_ts = self._clock.utc_now()
                try:
                    twap = parse_twap(
                        payload,
                        asset=asset,
                        window_seconds=60,
                        recv_ts=recv_ts,
                        normalized_ts=self._clock.utc_now(),
                        recv_monotonic_ns=self._clock.monotonic_ns(),
                    )
                except MarketDataSchemaError:
                    continue
                self._record(twap)
                parsed += 1
                break
        if parsed:
            self._message_count += parsed
            self._last_message_at = self._clock.utc_now()
            self._last_error = None
        return parsed

    def opening_twap(
        self,
        asset: Asset,
        *,
        boundary: datetime,
        tolerance: timedelta,
    ) -> ChainlinkTwap | None:
        require_utc("boundary", boundary)
        history = self._history.get(asset)
        if not history:
            return None
        best = min(history, key=lambda item: abs(item.publisher_ts - boundary))
        if abs(best.publisher_ts - boundary) > tolerance:
            return None
        return best

    def status(self) -> ChainlinkCollectorStatus:
        return ChainlinkCollectorStatus(
            connection="connected" if self._connected else "disconnected",
            last_message_at=self._last_message_at,
            reconnect_count=self._reconnect_count,
            message_count=self._message_count,
            history_size_by_asset={
                asset.value: len(self._history.get(asset, ())) for asset in self._assets
            },
            latest_twap_by_asset={
                asset.value: str(twap.value) for asset, twap in self._latest.items()
            },
            last_error=self._last_error,
        )

    def _record(self, twap: ChainlinkTwap) -> None:
        latest = self._latest.get(twap.asset)
        if latest is None or twap.publisher_ts >= latest.publisher_ts:
            self._latest[twap.asset] = twap
        history = self._history.setdefault(twap.asset, deque(maxlen=4096))
        if not history or (
            history[-1].publisher_ts != twap.publisher_ts or history[-1].value != twap.value
        ):
            history.append(twap)


class BinanceHourlyOfficialCollector:
    """Rule-defined 1h candle official source; never handles proxy depth/mid."""

    def __init__(self, transport: PublicTransport, clock: SystemClock) -> None:
        self._transport = transport
        self._clock = clock
        self._last_request_at: datetime | None = None
        self._last_candle_by_asset: dict[Asset, BinanceHourlyCandle] = {}
        self._last_error: str | None = None

    async def fetch_candle(self, discovery: MarketDiscovery) -> BinanceHourlyCandle:
        market = discovery.market
        if market.horizon is not Horizon.ONE_HOUR:
            raise ReferenceUnavailableError("Binance hourly official source is 1h-only")
        self._last_request_at = self._clock.utc_now()
        params = {
            "symbol": f"{market.asset.value}USDT",
            "interval": "1h",
            "startTime": str(int(market.window_start.timestamp() * 1000)),
            "endTime": str(int(market.window_end.timestamp() * 1000)),
            "limit": "1",
        }
        try:
            raw = await self._transport.get_json(_BINANCE_KLINES_URL, params=params)
            candle = _parse_binance_hourly_candle(raw, asset=market.asset, clock=self._clock)
            self._last_candle_by_asset[market.asset] = candle
            self._last_error = None
            return candle
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise

    def status(self) -> BinanceHourlyCollectorStatus:
        return BinanceHourlyCollectorStatus(
            last_request_at=self._last_request_at,
            last_candle_by_asset={
                asset.value: candle.open_time.isoformat()
                for asset, candle in self._last_candle_by_asset.items()
            },
            last_error=self._last_error,
        )


class OfficialPriceToBeatService:
    """Resolve official references and PTB without proxy fallbacks."""

    def __init__(
        self,
        *,
        repository: SQLitePriceToBeatRepository,
        chainlink: ChainlinkTwapCollector,
        binance_hourly: BinanceHourlyOfficialCollector,
        policy: ReferenceFreshnessPolicy,
    ) -> None:
        self._repository = repository
        self._chainlink = chainlink
        self._binance_hourly = binance_hourly
        self._policy = policy

    async def resolve(
        self,
        discovery: MarketDiscovery,
        *,
        observed_at: datetime,
    ) -> PriceToBeatResolution:
        require_utc("observed_at", observed_at)
        identity = _ptb_identity(discovery)
        restored = self._repository.get(identity)
        if restored is not None:
            try:
                record = restore_price_to_beat(
                    discovery,
                    restored,
                    restored_at=observed_at,
                    policy=self._policy,
                )
                return PriceToBeatResolution(
                    record,
                    record.reference,
                    "PTB_READY",
                    "PTB_RESTORED",
                    restored=True,
                )
            except ReferenceUnavailableError as exc:
                return PriceToBeatResolution(
                    None,
                    restored.reference,
                    "PTB_UNAVAILABLE",
                    f"PTB_RESTORE_REJECTED:{type(exc).__name__}",
                )

        if discovery.market.horizon is Horizon.ONE_HOUR:
            return await self._establish_hourly(discovery, identity)
        return self._establish_short(discovery, identity)

    def chainlink_status(self) -> ChainlinkCollectorStatus:
        return self._chainlink.status()

    def binance_hourly_status(self) -> BinanceHourlyCollectorStatus:
        return self._binance_hourly.status()

    def _establish_short(
        self,
        discovery: MarketDiscovery,
        identity: PriceToBeatIdentity,
    ) -> PriceToBeatResolution:
        market = discovery.market
        twap = self._chainlink.opening_twap(
            market.asset,
            boundary=market.window_start,
            tolerance=self._policy.boundary_tolerance,
        )
        if twap is None:
            return PriceToBeatResolution(None, None, "PTB_UNAVAILABLE", "BOUNDARY_HISTORY_MISSING")
        reference = official_reference_from_chainlink_twap(
            market,
            twap,
            recv_ts=twap.lineage.recv_ts,
            normalized_ts=twap.lineage.normalized_ts,
            is_price_to_beat=True,
        )
        return self._establish(discovery, identity, reference)

    async def _establish_hourly(
        self,
        discovery: MarketDiscovery,
        identity: PriceToBeatIdentity,
    ) -> PriceToBeatResolution:
        try:
            candle = await self._binance_hourly.fetch_candle(discovery)
            reference = official_reference_from_binance_hourly_candle(
                discovery.market,
                candle,
                is_price_to_beat=True,
            )
        except Exception as exc:
            return PriceToBeatResolution(
                None,
                None,
                "PTB_UNAVAILABLE",
                f"BINANCE_HOURLY_UNAVAILABLE:{type(exc).__name__}",
            )
        return self._establish(discovery, identity, reference)

    def _establish(
        self,
        discovery: MarketDiscovery,
        identity: PriceToBeatIdentity,
        reference: OfficialReference,
    ) -> PriceToBeatResolution:
        try:
            record = establish_price_to_beat(
                discovery,
                reference,
                observed_at=reference.recv_ts,
                persistence_id=identity.persistence_id,
                policy=self._policy,
            )
            saved = self._repository.save_once(identity, record)
            return PriceToBeatResolution(saved, saved.reference, "PTB_READY", "PTB_ESTABLISHED")
        except ReferenceUnavailableError as exc:
            return PriceToBeatResolution(
                None,
                reference,
                "PTB_UNAVAILABLE",
                f"PTB_ESTABLISH_REJECTED:{type(exc).__name__}",
            )


def _combined_twap_subscription(assets: Sequence[Asset]) -> dict[str, object]:
    subscriptions = []
    for asset in assets:
        message = twap_subscription(asset, window_seconds=60)
        items = message.get("subscriptions")
        if not isinstance(items, list):
            raise MarketDataSchemaError("Chainlink subscription had unexpected shape")
        subscriptions.extend(items)
    return {"action": "subscribe", "subscriptions": subscriptions}


def _iter_rtds_payloads(raw: object) -> tuple[object, ...]:
    if isinstance(raw, list):
        return tuple(raw)
    return (raw,)


def _parse_binance_hourly_candle(
    raw: object,
    *,
    asset: Asset,
    clock: SystemClock,
) -> BinanceHourlyCandle:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise MarketDataSchemaError("Binance klines response was not a non-empty array")
    first = raw[0]
    if not isinstance(first, Sequence) or isinstance(first, (str, bytes)) or len(first) < 5:
        raise MarketDataSchemaError("Binance kline row was malformed")
    try:
        open_time = utc_from_milliseconds(int(str(first[0])))
        open_price = Decimal(str(first[1]))
        close_price = Decimal(str(first[4]))
        close_time = open_time + timedelta(hours=1)
    except (ValueError, ArithmeticError) as exc:
        raise MarketDataSchemaError("Binance kline row had invalid values") from exc
    recv_ts = clock.utc_now()
    return BinanceHourlyCandle(
        asset=asset,
        open_time=open_time,
        close_time=close_time,
        open_price=open_price,
        close_price=close_price,
        lineage=EventLineage(
            source=DataSource.BINANCE_SPOT,
            source_ts=open_time,
            recv_ts=recv_ts,
            normalized_ts=recv_ts,
            recv_monotonic_ns=clock.monotonic_ns(),
        ),
    )


def _ptb_identity(discovery: MarketDiscovery) -> PriceToBeatIdentity:
    market = discovery.market
    return PriceToBeatIdentity(
        market.asset,
        market.horizon,
        market.market_id,
        market.condition_id,
        market.window_start,
        market.settlement_source,
    )
