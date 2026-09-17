"""Strict Binance public-feed endpoint and payload translation."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from direction_engine_v3.adapters._parsing import (
    JsonObject,
    require_bool,
    require_decimal,
    require_int,
    require_object,
    require_sequence,
    require_str,
)
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import (
    CryptoTopOfBook,
    CryptoTrade,
    DataSource,
    EventLineage,
    MarketDataSchemaError,
    utc_from_milliseconds,
)

SPOT_WS_BASE: Final = "wss://stream.binance.com:9443/ws"
PERPETUAL_WS_BASE: Final = "wss://fstream.binance.com/public/ws"
_SYMBOLS: Final = {asset: f"{asset.value.lower()}usdt" for asset in Asset}


def stream_url(asset: Asset, stream: str, *, perpetual: bool = False) -> str:
    """Build only an explicitly supported read-only public stream URL."""

    if not isinstance(asset, Asset):
        raise TypeError("asset must be an Asset")
    if stream not in {"aggTrade", "depth@100ms"}:
        raise ValueError("unsupported Binance stream")
    base = PERPETUAL_WS_BASE if perpetual else SPOT_WS_BASE
    return f"{base}/{_SYMBOLS[asset]}@{stream}"


def parse_aggregate_trade(
    raw: object,
    *,
    asset: Asset,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
    perpetual: bool = False,
) -> CryptoTrade:
    payload = require_object(raw)
    _require_symbol(payload, asset)
    if require_str(payload, "e") != "aggTrade":
        raise MarketDataSchemaError("unexpected Binance event type")
    return CryptoTrade(
        asset=asset,
        price=require_decimal(payload, "p"),
        quantity=require_decimal(payload, "q"),
        trade_id=require_int(payload, "a"),
        buyer_is_maker=require_bool(payload, "m"),
        lineage=_lineage(
            payload, recv_ts, normalized_ts, recv_monotonic_ns, perpetual=perpetual
        ),
    )


def parse_rest_aggregate_trade(
    raw: object,
    *,
    asset: Asset,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> CryptoTrade:
    """Parse the REST ``/api/v3/aggTrades`` contract.

    REST aggregate trades intentionally use a separate parser from the
    WebSocket ``aggTrade`` envelope.  The endpoint is symbol-scoped, so the
    requested ``asset`` is the canonical identity; an optional REST symbol is
    still checked when present.
    """

    payload = require_object(raw)
    symbol = payload.get("s")
    if symbol is not None and (
        not isinstance(symbol, str) or symbol != _SYMBOLS[asset].upper()
    ):
        raise MarketDataSchemaError("unexpected Binance REST symbol")
    source_ts = utc_from_milliseconds(require_int(payload, "T"))
    return CryptoTrade(
        asset=asset,
        price=require_decimal(payload, "p"),
        quantity=require_decimal(payload, "q"),
        trade_id=require_int(payload, "a"),
        buyer_is_maker=require_bool(payload, "m"),
        lineage=EventLineage(
            source=DataSource.BINANCE_SPOT,
            source_ts=source_ts,
            recv_ts=recv_ts,
            normalized_ts=normalized_ts,
            recv_monotonic_ns=recv_monotonic_ns,
        ),
    )


def parse_depth_top(
    raw: object,
    *,
    asset: Asset,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
    perpetual: bool = False,
) -> tuple[CryptoTopOfBook, int]:
    """Translate a depth event's best level and return its first update ID."""

    payload = require_object(raw)
    _require_symbol(payload, asset)
    if require_str(payload, "e") != "depthUpdate":
        raise MarketDataSchemaError("unexpected Binance event type")
    bid_price, bid_quantity = _top_level(payload.get("b"), "b")
    ask_price, ask_quantity = _top_level(payload.get("a"), "a")
    first_update_id = require_int(payload, "U")
    final_update_id = require_int(payload, "u")
    if final_update_id < first_update_id:
        raise MarketDataSchemaError("Binance update range is reversed")
    return (
        CryptoTopOfBook(
            asset=asset,
            bid_price=bid_price,
            bid_quantity=bid_quantity,
            ask_price=ask_price,
            ask_quantity=ask_quantity,
            update_id=final_update_id,
            lineage=_lineage(
                payload, recv_ts, normalized_ts, recv_monotonic_ns, perpetual=perpetual
            ),
        ),
        first_update_id,
    )


def _lineage(
    payload: JsonObject,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
    *,
    perpetual: bool,
) -> EventLineage:
    return EventLineage(
        source=DataSource.BINANCE_PERPETUAL if perpetual else DataSource.BINANCE_SPOT,
        source_ts=utc_from_milliseconds(require_int(payload, "E")),
        recv_ts=recv_ts,
        normalized_ts=normalized_ts,
        recv_monotonic_ns=recv_monotonic_ns,
    )


def _require_symbol(payload: JsonObject, asset: Asset) -> None:
    expected = _SYMBOLS[asset].upper()
    if require_str(payload, "s") != expected:
        raise MarketDataSchemaError(f"unexpected Binance symbol; expected {expected}")


def _top_level(levels: object, name: str) -> tuple[Decimal, Decimal]:
    values = require_sequence(levels, name)
    if not values:
        raise MarketDataSchemaError(f"{name} must contain a top level")
    level = require_sequence(values[0], f"{name}[0]")
    if len(level) != 2:
        raise MarketDataSchemaError(f"{name}[0] must contain price and quantity")
    try:
        price = Decimal(str(level[0]))
        quantity = Decimal(str(level[1]))
    except InvalidOperation as exc:
        raise MarketDataSchemaError(f"{name}[0] must contain decimals") from exc
    if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
        raise MarketDataSchemaError(f"{name}[0] must contain positive finite decimals")
    return price, quantity
