"""Polymarket RTDS translation for Chainlink crypto TWAP messages."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from direction_engine_v3.adapters._parsing import require_int, require_object, require_str
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
    data = require_object(payload.get("payload"), "payload")
    symbol = require_str(data, "symbol").replace("-", "/").upper()
    if symbol != f"{asset.value}/USD":
        raise MarketDataSchemaError("unexpected Chainlink TWAP symbol")
    full_accuracy = require_str(data, "full_accuracy_value")
    try:
        scaled_integer = Decimal(full_accuracy)
    except InvalidOperation as exc:
        raise MarketDataSchemaError("full_accuracy_value must be an E18 integer") from exc
    if not scaled_integer.is_finite() or scaled_integer != scaled_integer.to_integral_value():
        raise MarketDataSchemaError("full_accuracy_value must be an E18 integer")
    source_ts = utc_from_milliseconds(require_int(data, "timestamp"))
    publisher_ts = utc_from_milliseconds(require_int(payload, "timestamp"))
    return ChainlinkTwap(
        asset=asset,
        value=scaled_integer / _SCALE,
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
