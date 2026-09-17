"""Runtime official-reference collectors and PTB boundary resolution."""

import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.adapters.chainlink.rtds import (
    RTDS_HEARTBEAT_SECONDS,
    RTDS_WS_URL,
    inspect_twap_frame,
    parse_twap_from_symbol,
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
    selected_topic: str
    subscription_status: str
    last_message_at: datetime | None
    last_source_timestamp: datetime | None
    reconnect_count: int
    message_count: int
    parse_success_count: int
    parse_failure_count: int
    subscription_snapshot_count: int
    history_size_by_asset: Mapping[str, int]
    latest_twap_by_asset: Mapping[str, str]
    last_error: str | None
    per_asset: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "connection": self.connection,
            "selected_topic": self.selected_topic,
            "subscription_status": self.subscription_status,
            "last_message_at": self.last_message_at.isoformat()
            if self.last_message_at is not None
            else None,
            "last_source_timestamp": self.last_source_timestamp.isoformat()
            if self.last_source_timestamp is not None
            else None,
            "reconnect_count": self.reconnect_count,
            "message_count": self.message_count,
            "parse_success_count": self.parse_success_count,
            "parse_failure_count": self.parse_failure_count,
            "subscription_snapshot_count": self.subscription_snapshot_count,
            "history_size_by_asset": dict(self.history_size_by_asset),
            "latest_twap_by_asset": dict(self.latest_twap_by_asset),
            "last_error": self.last_error,
            "per_asset": {asset: dict(status) for asset, status in self.per_asset.items()},
        }


@dataclass(frozen=True, slots=True)
class BinanceHourlyCollectorStatus:
    last_request_at: datetime | None
    last_http_status: str | None
    last_market_window_start: datetime | None
    last_match_status: str | None
    last_candle_by_asset: Mapping[str, str]
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "last_request_at": self.last_request_at.isoformat()
            if self.last_request_at is not None
            else None,
            "last_http_status": self.last_http_status,
            "last_market_window_start": self.last_market_window_start.isoformat()
            if self.last_market_window_start is not None
            else None,
            "last_match_status": self.last_match_status,
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


@dataclass(slots=True)
class _AssetCollectorState:
    connected: bool = False
    subscription_status: str = "NOT_STARTED"
    reconnect_count: int = 0
    message_count: int = 0
    parse_success_count: int = 0
    parse_failure_count: int = 0
    last_message_at: datetime | None = None
    last_source_timestamp: datetime | None = None
    latest_twap: ChainlinkTwap | None = None
    last_error: str | None = None
    filter_match_count: int = 0
    filter_mismatch_count: int = 0
    subscription_snapshot_count: int = 0
    last_filter_status: str = "NO_DATA"
    last_frame_class: str = "NO_DATA"
    last_returned_symbol: str | None = None


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
        if not self._assets or len(set(self._assets)) != len(self._assets):
            raise ValueError("assets must be a non-empty unique sequence")
        self._history: dict[Asset, deque[ChainlinkTwap]] = {
            asset: deque(maxlen=history_limit) for asset in self._assets
        }
        self._latest: dict[Asset, ChainlinkTwap] = {}
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=8)
        self._message_count = 0
        self._parse_success_count = 0
        self._parse_failure_count = 0
        self._subscription_snapshot_count = 0
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None
        self._subscription_status = "NOT_STARTED"
        self._asset_state: dict[Asset, _AssetCollectorState] = {
            asset: _AssetCollectorState() for asset in self._assets
        }

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run independent bounded RTDS subscriptions until stopped."""

        tasks = [
            asyncio.create_task(self._run_asset(asset, stop_event), name=f"chainlink-{asset.value}")
            for asset in self._assets
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_asset(self, asset: Asset, stop_event: asyncio.Event) -> None:
        state = self._asset_state[asset]
        while not stop_event.is_set():
            try:
                state.connected = True
                state.subscription_status = "SUBSCRIBED"
                async for raw in self._transport.resilient_websocket_json(
                    RTDS_WS_URL,
                    subscription=twap_subscription(asset, window_seconds=60),
                    text_heartbeat_seconds=RTDS_HEARTBEAT_SECONDS,
                    retry_policy=self._retry_policy,
                ):
                    if stop_event.is_set():
                        break
                    self.handle_message(raw, asset_hint=asset)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                state.subscription_status = "ERROR"
                state.reconnect_count += 1
            finally:
                state.connected = False
            if not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                except TimeoutError:
                    continue

    def handle_message(self, raw: object, *, asset_hint: Asset | None = None) -> int:
        """Parse one RTDS message and record every valid supported 60s TWAP."""

        parsed = 0
        for payload in _iter_rtds_payloads(raw):
            intended_asset = asset_hint
            try:
                frame = inspect_twap_frame(payload, window_seconds=60)
                if frame.frame_class == "SUBSCRIPTION_SNAPSHOT":
                    self._record_subscription_snapshot(frame.asset, intended_asset)
                    continue
                recv_ts = self._clock.utc_now()
                twap = parse_twap_from_symbol(
                    payload,
                    window_seconds=60,
                    recv_ts=recv_ts,
                    normalized_ts=self._clock.utc_now(),
                    recv_monotonic_ns=self._clock.monotonic_ns(),
                )
            except MarketDataSchemaError as exc:
                self._record_parse_failure(intended_asset, exc)
                continue
            self._record(twap)
            state = self._asset_state[twap.asset]
            state.message_count += 1
            state.parse_success_count += 1
            state.last_message_at = self._clock.utc_now()
            state.last_error = None
            state.last_returned_symbol = f"{twap.asset.value}/USD"
            state.last_frame_class = "LIVE_UPDATE"
            if intended_asset is None or intended_asset is twap.asset:
                state.filter_match_count += 1
                state.last_filter_status = "FILTER_MATCH"
            else:
                intended_state = self._asset_state[intended_asset]
                intended_state.filter_mismatch_count += 1
                intended_state.last_filter_status = "FILTER_MISMATCH"
                intended_state.last_returned_symbol = f"{twap.asset.value}/USD"
                intended_state.last_error = (
                    f"FILTER_MISMATCH:intended={intended_asset.value}:"
                    f"returned={twap.asset.value}"
                )
                state.last_filter_status = "ROUTED_BY_RETURNED_SYMBOL"
            parsed += 1
        if parsed:
            self._message_count += parsed
            self._parse_success_count += parsed
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
        per_asset = {
            asset.value: {
                "connection": "connected" if state.connected else "disconnected",
                "subscription_status": state.subscription_status,
                "reconnect_count": state.reconnect_count,
                "message_count": state.message_count,
                "parse_success_count": state.parse_success_count,
                "parse_failure_count": state.parse_failure_count,
                "subscription_snapshot_count": state.subscription_snapshot_count,
                "last_message_at": state.last_message_at.isoformat()
                if state.last_message_at is not None
                else None,
                "last_source_timestamp": state.last_source_timestamp.isoformat()
                if state.last_source_timestamp is not None
                else None,
                "history_size": len(self._history.get(asset, ())),
                "latest_twap": str(state.latest_twap.value)
                if state.latest_twap is not None
                else None,
                "last_error": state.last_error,
                "filter_match_count": state.filter_match_count,
                "filter_mismatch_count": state.filter_mismatch_count,
                "last_frame_class": state.last_frame_class,
                "last_filter_status": state.last_filter_status,
                "last_returned_symbol": state.last_returned_symbol,
            }
            for asset, state in self._asset_state.items()
        }
        connected_count = sum(1 for state in self._asset_state.values() if state.connected)
        subscribed_count = sum(
            1 for state in self._asset_state.values() if state.subscription_status == "SUBSCRIBED"
        )
        error_count = sum(
            1 for state in self._asset_state.values() if state.subscription_status == "ERROR"
        )
        aggregate_subscription = (
            "SUBSCRIBED"
            if subscribed_count == len(self._assets)
            else "PARTIAL"
            if subscribed_count
            else "ERROR"
            if error_count
            else self._subscription_status
        )
        source_timestamps = [
            state.last_source_timestamp
            for state in self._asset_state.values()
            if state.last_source_timestamp is not None
        ]
        errors = [state.last_error for state in self._asset_state.values() if state.last_error]
        return ChainlinkCollectorStatus(
            connection="connected" if connected_count else "disconnected",
            selected_topic="crypto_prices_twap_sixty",
            subscription_status=aggregate_subscription,
            last_message_at=self._last_message_at,
            last_source_timestamp=max(source_timestamps) if source_timestamps else None,
            reconnect_count=sum(state.reconnect_count for state in self._asset_state.values()),
            message_count=self._message_count,
            parse_success_count=self._parse_success_count,
            parse_failure_count=self._parse_failure_count,
            subscription_snapshot_count=self._subscription_snapshot_count,
            history_size_by_asset={
                asset.value: len(self._history.get(asset, ())) for asset in self._assets
            },
            latest_twap_by_asset={
                asset.value: str(twap.value) for asset, twap in self._latest.items()
            },
            last_error=errors[-1] if errors else self._last_error,
            per_asset=per_asset,
        )

    def _record(self, twap: ChainlinkTwap) -> None:
        latest = self._latest.get(twap.asset)
        if latest is None or twap.publisher_ts >= latest.publisher_ts:
            self._latest[twap.asset] = twap
            self._asset_state[twap.asset].latest_twap = twap
            self._asset_state[twap.asset].last_source_timestamp = twap.publisher_ts
        history = self._history.setdefault(twap.asset, deque(maxlen=4096))
        if not history or (
            history[-1].publisher_ts != twap.publisher_ts or history[-1].value != twap.value
        ):
            history.append(twap)

    def _record_subscription_snapshot(
        self,
        returned_asset: Asset,
        intended_asset: Asset | None,
    ) -> None:
        state = self._asset_state[returned_asset]
        state.subscription_snapshot_count += 1
        state.last_frame_class = "SUBSCRIPTION_SNAPSHOT"
        state.last_returned_symbol = f"{returned_asset.value}/USD"
        if intended_asset is None or intended_asset is returned_asset:
            state.filter_match_count += 1
            state.last_filter_status = "FILTER_MATCH"
        else:
            intended_state = self._asset_state[intended_asset]
            intended_state.filter_mismatch_count += 1
            intended_state.last_filter_status = "FILTER_MISMATCH"
            intended_state.last_frame_class = "SUBSCRIPTION_SNAPSHOT"
            intended_state.last_returned_symbol = f"{returned_asset.value}/USD"
        self._subscription_snapshot_count += 1

    def _record_parse_failure(
        self,
        intended_asset: Asset | None,
        exc: MarketDataSchemaError,
    ) -> None:
        targets = (intended_asset,) if intended_asset is not None else self._assets
        for asset in targets:
            state = self._asset_state[asset]
            state.parse_failure_count += 1
            state.last_error = str(exc)[:500]
        self._parse_failure_count += 1


class BinanceHourlyOfficialCollector:
    """Rule-defined 1h candle official source; never handles proxy depth/mid."""

    def __init__(self, transport: PublicTransport, clock: SystemClock) -> None:
        self._transport = transport
        self._clock = clock
        self._last_request_at: datetime | None = None
        self._last_candle_by_asset: dict[Asset, BinanceHourlyCandle] = {}
        self._last_error: str | None = None
        self._last_http_status: str | None = None
        self._last_market_window_start: datetime | None = None
        self._last_match_status: str | None = None

    async def fetch_candle(self, discovery: MarketDiscovery) -> BinanceHourlyCandle:
        market = discovery.market
        if market.horizon is not Horizon.ONE_HOUR:
            raise ReferenceUnavailableError("Binance hourly official source is 1h-only")
        self._last_request_at = self._clock.utc_now()
        self._last_market_window_start = market.window_start
        params = {
            "symbol": f"{market.asset.value}USDT",
            "interval": "1h",
            "startTime": str(int(market.window_start.timestamp() * 1000)),
            "endTime": str(int(market.window_end.timestamp() * 1000)),
            "limit": "1",
        }
        try:
            raw = await self._transport.get_json(_BINANCE_KLINES_URL, params=params)
            self._last_http_status = "OK"
            candle = _parse_binance_hourly_candle(raw, asset=market.asset, clock=self._clock)
            self._last_candle_by_asset[market.asset] = candle
            self._last_match_status = (
                "MATCH"
                if candle.open_time == market.window_start
                else "BINANCE_HOURLY_WINDOW_MISMATCH"
            )
            self._last_error = None
            return candle
        except Exception as exc:
            self._last_http_status = "ERROR"
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise

    def status(self) -> BinanceHourlyCollectorStatus:
        return BinanceHourlyCollectorStatus(
            last_request_at=self._last_request_at,
            last_http_status=self._last_http_status,
            last_market_window_start=self._last_market_window_start,
            last_match_status=self._last_match_status,
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
            return PriceToBeatResolution(
                None,
                None,
                "PTB_UNAVAILABLE",
                self._short_unavailable_reason(market.asset),
            )
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
            reason = (
                "BINANCE_HOURLY_WINDOW_MISMATCH"
                if isinstance(exc, ReferenceUnavailableError)
                else "BINANCE_HOURLY_HTTP_FAILED"
            )
            return PriceToBeatResolution(
                None,
                None,
                "PTB_UNAVAILABLE",
                f"{reason}:{type(exc).__name__}",
            )
        return self._establish(discovery, identity, reference)

    def _short_unavailable_reason(self, asset: Asset) -> str:
        status = self._chainlink.status()
        if status.message_count == 0:
            if status.last_error:
                return "CHAINLINK_SOCKET_UNAVAILABLE"
            return "CHAINLINK_NO_MESSAGES"
        if status.parse_success_count == 0 and status.parse_failure_count > 0:
            return "CHAINLINK_PARSE_FAILED"
        if status.history_size_by_asset.get(asset.value, 0) == 0:
            return "BOUNDARY_HISTORY_MISSING"
        return "BOUNDARY_SAMPLE_OUTSIDE_TOLERANCE"

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
            if discovery.market.horizon is Horizon.ONE_HOUR and "Binance candle" in str(exc):
                reason = "BINANCE_HOURLY_WINDOW_MISMATCH"
            else:
                reason = "PTB_ESTABLISH_REJECTED"
            return PriceToBeatResolution(
                None,
                reference,
                "PTB_UNAVAILABLE",
                f"{reason}:{type(exc).__name__}",
            )


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
