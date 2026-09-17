"""Polymarket RTDS translation for Chainlink crypto TWAP messages."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from direction_engine_v3.adapters._parsing import (
    require_decimal,
    require_int,
    require_object,
    require_sequence,
    require_str,
)
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import (
    ChainlinkTwap,
    DataSource,
    EventLineage,
    MarketDataSchemaError,
    utc_from_milliseconds,
)

RTDS_WS_URL: Final = "wss://ws-live-data.polymarket.com"
RTDS_HEARTBEAT_SECONDS: Final = 5.0
_TOPICS: Final = {30: "crypto_prices_twap_thirty", 60: "crypto_prices_twap_sixty"}
_SCALE: Final = Decimal(10) ** 18


@dataclass(frozen=True, slots=True)
class ChainlinkTwapFrame:
    frame_class: str
    asset: Asset
    topic: str
    message_type: str
    symbol: str
    payload_keys: tuple[str, ...]
    data_item_count: int | None
    data_item_keys: tuple[str, ...]
    window_s: int | None


def twap_subscription(asset: Asset, *, window_seconds: int) -> dict[str, object]:
    if window_seconds not in _TOPICS:
        raise ValueError("window_seconds must be 30 or 60")
    return {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": _TOPICS[window_seconds],
                "type": "update",
                "filters": f'{{"symbol":"{asset.value.lower()}/usd"}}',
            }
        ],
    }


def inspect_twap_frame(raw: object, *, window_seconds: int) -> ChainlinkTwapFrame:
    """Validate the RTDS TWAP envelope and derive the authoritative asset."""

    payload = require_object(raw)
    if window_seconds not in _TOPICS:
        raise ValueError("window_seconds must be 30 or 60")
    topic = require_str(payload, "topic")
    if topic != _TOPICS[window_seconds]:
        raise MarketDataSchemaError("unexpected Chainlink TWAP topic")
    message_type = require_str(payload, "type")
    if message_type not in {"update", "subscribe"}:
        raise MarketDataSchemaError("unexpected Chainlink TWAP message type")
    envelope = require_object(payload.get("payload"), "payload")
    symbol = require_str(envelope, "symbol").replace("-", "/").upper()
    asset = _asset_from_symbol(symbol)
    window_s = require_int(envelope, "window_s") if "window_s" in envelope else None
    if window_s is not None and window_s != window_seconds:
        raise MarketDataSchemaError("unexpected Chainlink TWAP window")
    data_item_count: int | None = None
    data_item_keys: tuple[str, ...] = ()
    if "data" in envelope:
        raw_data = envelope.get("data")
        if isinstance(raw_data, Mapping):
            data_item_count = 1
            data_item_keys = _sorted_keys(raw_data)
        else:
            values = require_sequence(raw_data, "data")
            data_item_count = len(values)
            if len(values) == 1:
                data_item_keys = _sorted_keys(require_object(values[0], "data[0]"))
    frame_class = _classify_frame(message_type, envelope)
    return ChainlinkTwapFrame(
        frame_class=frame_class,
        asset=asset,
        topic=topic,
        message_type=message_type,
        symbol=symbol,
        payload_keys=_sorted_keys(envelope),
        data_item_count=data_item_count,
        data_item_keys=data_item_keys,
        window_s=window_s,
    )


def parse_twap_from_symbol(
    raw: object,
    *,
    window_seconds: int,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> ChainlinkTwap:
    """Parse a TWAP using the validated returned symbol as asset identity."""

    frame = inspect_twap_frame(raw, window_seconds=window_seconds)
    return parse_twap(
        raw,
        asset=frame.asset,
        window_seconds=window_seconds,
        recv_ts=recv_ts,
        normalized_ts=normalized_ts,
        recv_monotonic_ns=recv_monotonic_ns,
    )


def parse_twap(
    raw: object,
    *,
    asset: Asset,
    window_seconds: int,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> ChainlinkTwap:
    payload = require_object(raw)
    if window_seconds not in _TOPICS or require_str(payload, "topic") != _TOPICS[window_seconds]:
        raise MarketDataSchemaError("unexpected Chainlink TWAP topic")
    if require_str(payload, "type") != "update":
        raise MarketDataSchemaError("unexpected Chainlink TWAP message type")
    envelope = require_object(payload.get("payload"), "payload")
    symbol = require_str(envelope, "symbol").replace("-", "/").upper()
    if symbol != f"{asset.value}/USD":
        raise MarketDataSchemaError("unexpected Chainlink TWAP symbol")

    record = _normalize_twap_record(envelope, window_seconds=window_seconds)
    full_accuracy = require_decimal(record, "full_accuracy_value")
    if (
        not full_accuracy.is_finite()
        or full_accuracy <= 0
        or full_accuracy != full_accuracy.to_integral_value()
    ):
        raise MarketDataSchemaError("full_accuracy_value must be an E18 integer")
    value_raw = record.get("value")
    if value_raw is None:
        raise MarketDataSchemaError("value is required for verified Chainlink TWAP data")
    value = require_decimal(record, "value")
    if not value.is_finite() or value <= 0:
        raise MarketDataSchemaError("value must be a positive finite decimal")
    source_ts = utc_from_milliseconds(require_int(record, "timestamp"))
    publisher_ts = utc_from_milliseconds(require_int(payload, "timestamp"))
    return ChainlinkTwap(
        asset=asset,
        value=full_accuracy / _SCALE,
        window_seconds=window_seconds,
        publisher_ts=publisher_ts,
        lineage=EventLineage(
            source=DataSource.CHAINLINK_RTDS,
            source_ts=source_ts,
            recv_ts=recv_ts,
            normalized_ts=normalized_ts,
            recv_monotonic_ns=recv_monotonic_ns,
        ),
    )


def _asset_from_symbol(symbol: str) -> Asset:
    for asset in Asset:
        if symbol == f"{asset.value}/USD":
            return asset
    raise MarketDataSchemaError("unsupported Chainlink TWAP symbol")


def _sorted_keys(value: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in value))


def _normalize_twap_record(
    envelope: Mapping[str, object], *, window_seconds: int
) -> Mapping[str, object]:
    """Normalize verified RTDS update envelopes without loose fallbacks."""

    if "data" not in envelope:
        if require_int(envelope, "window_s") != window_seconds:
            raise MarketDataSchemaError("unexpected Chainlink TWAP window")
        return envelope

    if require_int(envelope, "window_s") != window_seconds:
        raise MarketDataSchemaError("unexpected Chainlink TWAP window")
    raw_data = envelope.get("data")
    if isinstance(raw_data, Mapping):
        return raw_data
    values = require_sequence(raw_data, "data")
    if len(values) != 1:
        raise MarketDataSchemaError("Chainlink TWAP data must contain exactly one record")
    return require_object(values[0], "data[0]")


def _classify_frame(message_type: str, envelope: Mapping[str, object]) -> str:
    if message_type == "subscribe":
        if "data" not in envelope:
            raise MarketDataSchemaError("Chainlink subscribe frame missing snapshot data")
        require_sequence(envelope.get("data"), "data")
        return "SUBSCRIPTION_SNAPSHOT"
    if message_type == "update":
        return "LIVE_UPDATE"
    raise MarketDataSchemaError("unexpected Chainlink TWAP message type")
