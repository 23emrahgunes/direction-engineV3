"""Strict translation for public Polymarket Gamma and CLOB data."""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from direction_engine_v3.adapters._parsing import (
    JsonObject,
    require_decimal,
    require_int,
    require_iso8601,
    require_object,
    require_sequence,
    require_str,
)
from direction_engine_v3.domain import Asset, Horizon, Market, MarketToken, OutcomeSide
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    MarketDataSchemaError,
    PolymarketBook,
    PolymarketLevel,
    PolymarketResolutionEvent,
    utc_from_milliseconds,
)

GAMMA_MARKETS_URL: Final = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL: Final = "https://clob.polymarket.com/book"
CLOB_MARKETS_URL: Final = "https://clob.polymarket.com/clob-markets"
CLOB_WS_URL: Final = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def market_subscription(token_ids: tuple[str, ...]) -> dict[str, object]:
    """Create the documented public market-channel subscription."""

    if not token_ids or any(not token_id or token_id != token_id.strip() for token_id in token_ids):
        raise ValueError("token_ids must contain non-empty trimmed values")
    if len(set(token_ids)) != len(token_ids):
        raise ValueError("token_ids must be unique")
    return {"assets_ids": list(token_ids), "type": "market"}


def parse_gamma_market(raw: object, *, asset: Asset, horizon: Horizon) -> Market:
    """Map outcome labels to token IDs explicitly; array position alone is never trusted."""

    payload = require_object(raw)
    outcomes = _array_field(payload, "outcomes")
    token_ids = _array_field(payload, "clobTokenIds")
    if len(outcomes) != 2 or len(token_ids) != 2:
        raise MarketDataSchemaError("binary market must contain exactly two outcomes and token IDs")
    tokens: list[MarketToken] = []
    for outcome, token_id in zip(outcomes, token_ids, strict=True):
        if not isinstance(outcome, str) or not isinstance(token_id, str):
            raise MarketDataSchemaError("outcomes and token IDs must be strings")
        normalized = outcome.strip().upper()
        aliases = {"UP": OutcomeSide.UP, "DOWN": OutcomeSide.DOWN}
        if normalized not in aliases:
            raise MarketDataSchemaError("market outcomes must map explicitly to UP and DOWN")
        tokens.append(MarketToken(token_id=token_id, outcome=aliases[normalized]))
    return Market(
        market_id=require_str(payload, "id"),
        condition_id=require_str(payload, "conditionId"),
        asset=asset,
        horizon=horizon,
        tokens=tuple(tokens),
        window_start=require_iso8601(payload, "startDate"),
        window_end=require_iso8601(payload, "endDate"),
        settlement_source=require_str(payload, "resolutionSource"),
    )


def parse_clob_book(
    raw: object,
    *,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> PolymarketBook:
    payload = require_object(raw)
    return PolymarketBook(
        condition_id=require_str(payload, "market"),
        token_id=require_str(payload, "asset_id"),
        bids=_levels(payload.get("bids"), "bids", reverse=True),
        asks=_levels(payload.get("asks"), "asks", reverse=False),
        checksum=require_str(payload, "hash"),
        minimum_order_size=require_decimal(payload, "min_order_size"),
        tick_size=require_decimal(payload, "tick_size"),
        lineage=EventLineage(
            source=DataSource.POLYMARKET_CLOB,
            source_ts=utc_from_milliseconds(require_int(payload, "timestamp")),
            recv_ts=recv_ts,
            normalized_ts=normalized_ts,
            recv_monotonic_ns=recv_monotonic_ns,
        ),
    )


def parse_fee_schedule(
    raw: object,
    *,
    condition_id: str,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> FeeSchedule:
    """Parse current CLOB fee data without hard-coded fee assumptions."""

    payload = require_object(raw)
    fee = require_object(payload.get("fd"), "fd")
    return FeeSchedule(
        condition_id=condition_id,
        maker_base_bps=require_decimal(payload, "mbf"),
        taker_base_bps=require_decimal(payload, "tbf"),
        rate=_optional_decimal(fee, "r"),
        exponent=_optional_decimal(fee, "e"),
        lineage=EventLineage(
            source=DataSource.POLYMARKET_CLOB,
            source_ts=None,
            recv_ts=recv_ts,
            normalized_ts=normalized_ts,
            recv_monotonic_ns=recv_monotonic_ns,
        ),
    )


def parse_market_resolved(
    raw: object,
    *,
    recv_ts: datetime,
    normalized_ts: datetime,
    recv_monotonic_ns: int,
) -> PolymarketResolutionEvent:
    """Parse the documented public market-channel resolution event."""

    payload = require_object(raw)
    if require_str(payload, "event_type") != "market_resolved":
        raise MarketDataSchemaError("unexpected Polymarket lifecycle event")
    return PolymarketResolutionEvent(
        market_id=require_str(payload, "id"),
        condition_id=require_str(payload, "market"),
        winning_token_id=require_str(payload, "winning_asset_id"),
        winning_outcome=require_str(payload, "winning_outcome"),
        lineage=EventLineage(
            source=DataSource.POLYMARKET_CLOB,
            source_ts=utc_from_milliseconds(require_int(payload, "timestamp")),
            recv_ts=recv_ts,
            normalized_ts=normalized_ts,
            recv_monotonic_ns=recv_monotonic_ns,
        ),
    )


def _array_field(payload: JsonObject, key: str) -> tuple[object, ...]:
    value = payload.get(key)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MarketDataSchemaError(f"{key} must contain a JSON array") from exc
    return tuple(require_sequence(value, key))


def _levels(value: object, name: str, *, reverse: bool) -> tuple[PolymarketLevel, ...]:
    levels = require_sequence(value, name)
    parsed: list[PolymarketLevel] = []
    for index, raw_level in enumerate(levels):
        level = require_object(raw_level, f"{name}[{index}]")
        parsed.append(
            PolymarketLevel(
                price=require_decimal(level, "price"),
                quantity=require_decimal(level, "size"),
            )
        )
    return tuple(sorted(parsed, key=lambda item: item.price, reverse=reverse))


def _optional_decimal(payload: JsonObject, key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise MarketDataSchemaError(f"fee.{key} must be decimal-compatible") from exc
    if not parsed.is_finite():
        raise MarketDataSchemaError(f"fee.{key} must be finite")
    return parsed
