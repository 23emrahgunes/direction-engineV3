"""Polymarket RTDS translation for Chainlink crypto TWAP messages."""

from collections.abc import Mapping
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
    if "data" in envelope and value_raw is None:
        raise MarketDataSchemaError("value is required for verified Chainlink TWAP data")
    if value_raw is not None:
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


def _normalize_twap_record(
    envelope: Mapping[str, object], *, window_seconds: int
) -> Mapping[str, object]:
    """Normalize the verified RTDS payload.data envelope without fallbacks."""

    if "data" not in envelope:
        # Preserve the already accepted legacy fixture shape while keeping its
        # strict fields. Production live messages use the branch below.
        if "window_s" in envelope:
            raise MarketDataSchemaError("Chainlink TWAP data must be an array or object")
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
