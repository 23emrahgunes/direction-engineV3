"""Immutable normalized contracts for public market data."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from direction_engine_v3.domain import Asset
from direction_engine_v3.domain._validation import (
    require_decimal,
    require_text,
    require_time_order,
    require_utc,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class DataSource(StrEnum):
    """Closed source identities used in V3.2."""

    BINANCE_SPOT = "BINANCE_SPOT"
    BINANCE_PERPETUAL = "BINANCE_PERPETUAL"
    POLYMARKET_CLOB = "POLYMARKET_CLOB"
    POLYMARKET_GAMMA = "POLYMARKET_GAMMA"
    CHAINLINK_RTDS = "CHAINLINK_RTDS"


@dataclass(frozen=True, slots=True)
class EventLineage:
    """Separate source, receipt, normalization, and monotonic times."""

    source: DataSource
    source_ts: datetime | None
    recv_ts: datetime
    normalized_ts: datetime
    recv_monotonic_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, DataSource):
            raise TypeError("source must be a DataSource")
        if self.source_ts is not None:
            require_utc("source_ts", self.source_ts)
        require_time_order("recv_ts", self.recv_ts, "normalized_ts", self.normalized_ts)
        if isinstance(self.recv_monotonic_ns, bool) or not isinstance(self.recv_monotonic_ns, int):
            raise TypeError("recv_monotonic_ns must be an integer")
        if self.recv_monotonic_ns < 0:
            raise ValueError("recv_monotonic_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class CryptoTrade:
    """Normalized Binance trade event."""

    asset: Asset
    price: Decimal
    quantity: Decimal
    trade_id: int
    buyer_is_maker: bool
    lineage: EventLineage

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        require_decimal("price", self.price, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        if (
            isinstance(self.trade_id, bool)
            or not isinstance(self.trade_id, int)
            or self.trade_id < 0
        ):
            raise ValueError("trade_id must be a non-negative integer")
        if not isinstance(self.buyer_is_maker, bool):
            raise TypeError("buyer_is_maker must be a bool")
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class CryptoTopOfBook:
    """Normalized Binance best bid and ask."""

    asset: Asset
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal
    update_id: int
    lineage: EventLineage

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        for name, value in (
            ("bid_price", self.bid_price),
            ("bid_quantity", self.bid_quantity),
            ("ask_price", self.ask_price),
            ("ask_quantity", self.ask_quantity),
        ):
            require_decimal(name, value, minimum=_ZERO, minimum_exclusive=True)
        if self.bid_price >= self.ask_price:
            raise ValueError("top of book must not be locked or crossed")
        if isinstance(self.update_id, bool) or not isinstance(self.update_id, int):
            raise TypeError("update_id must be an integer")
        if self.update_id < 0:
            raise ValueError("update_id must be non-negative")
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class ChainlinkTwap:
    """Credential-free Chainlink TWAP published through Polymarket RTDS."""

    asset: Asset
    value: Decimal
    window_seconds: int
    publisher_ts: datetime
    lineage: EventLineage

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        require_decimal("value", self.value, minimum=_ZERO, minimum_exclusive=True)
        if self.window_seconds not in {30, 60}:
            raise ValueError("window_seconds must be 30 or 60")
        require_utc("publisher_ts", self.publisher_ts)
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Current market fee parameters with source lineage."""

    condition_id: str
    maker_base_bps: Decimal
    taker_base_bps: Decimal
    rate: Decimal | None
    exponent: Decimal | None
    lineage: EventLineage

    def __post_init__(self) -> None:
        require_text("condition_id", self.condition_id)
        require_decimal("maker_base_bps", self.maker_base_bps, minimum=_ZERO)
        require_decimal("taker_base_bps", self.taker_base_bps, minimum=_ZERO)
        if self.rate is not None:
            require_decimal("rate", self.rate, minimum=_ZERO)
        if self.exponent is not None:
            require_decimal("exponent", self.exponent, minimum=_ZERO)
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class PolymarketLevel:
    """One price/size level in the public CLOB payload."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        require_decimal("price", self.price, minimum=_ZERO, maximum=_ONE)
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)


@dataclass(frozen=True, slots=True)
class PolymarketBook:
    """Normalized public Polymarket CLOB snapshot."""

    condition_id: str
    token_id: str
    bids: tuple[PolymarketLevel, ...]
    asks: tuple[PolymarketLevel, ...]
    checksum: str
    minimum_order_size: Decimal
    tick_size: Decimal
    lineage: EventLineage

    def __post_init__(self) -> None:
        require_text("condition_id", self.condition_id)
        require_text("token_id", self.token_id)
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple):
            raise TypeError("bids and asks must be tuples")
        if any(not isinstance(level, PolymarketLevel) for level in (*self.bids, *self.asks)):
            raise TypeError("book levels must be PolymarketLevel values")
        if self.bids != tuple(sorted(self.bids, key=lambda level: level.price, reverse=True)):
            raise ValueError("bids must be sorted descending")
        if self.asks != tuple(sorted(self.asks, key=lambda level: level.price)):
            raise ValueError("asks must be sorted ascending")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("book must not be locked or crossed")
        require_text("checksum", self.checksum)
        require_decimal(
            "minimum_order_size", self.minimum_order_size, minimum=_ZERO, minimum_exclusive=True
        )
        require_decimal("tick_size", self.tick_size, minimum=_ZERO, minimum_exclusive=True)
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class PolymarketResolutionEvent:
    """Public market-resolved event; reconciliation must still verify settlement."""

    market_id: str
    condition_id: str
    winning_token_id: str
    winning_outcome: str
    lineage: EventLineage

    def __post_init__(self) -> None:
        require_text("market_id", self.market_id)
        require_text("condition_id", self.condition_id)
        require_text("winning_token_id", self.winning_token_id)
        require_text("winning_outcome", self.winning_outcome)
        if not isinstance(self.lineage, EventLineage):
            raise TypeError("lineage must be EventLineage")
