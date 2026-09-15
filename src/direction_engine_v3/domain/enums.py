"""Closed enumerations for the V3 domain."""

from enum import StrEnum


class Asset(StrEnum):
    """Assets supported by direction-engineV3."""

    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"


class Horizon(StrEnum):
    """Canonical market horizons."""

    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"


class OutcomeSide(StrEnum):
    """Binary market outcomes."""

    UP = "UP"
    DOWN = "DOWN"


class ReferenceKind(StrEnum):
    """Reference-data lineage kinds."""

    OFFICIAL = "OFFICIAL"
    PROXY = "PROXY"


class StrategyKind(StrEnum):
    """Independent strategy families."""

    DIRECTIONAL_EDGE = "DIRECTIONAL_EDGE"
    STRUCTURAL_ARBITRAGE = "STRUCTURAL_ARBITRAGE"
    DUAL40 = "DUAL40"


class DecisionAction(StrEnum):
    """A strategy decision outcome."""

    TRADE = "TRADE"
    ABSTAIN = "ABSTAIN"


class TradingMode(StrEnum):
    """Execution environment selected by an execution plan."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class OrderSide(StrEnum):
    """Order direction at the venue."""

    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(StrEnum):
    """Supported order lifetime semantics."""

    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class OrderStatus(StrEnum):
    """Order lifecycle states.

    ACKNOWLEDGED is intentionally distinct from both partially and fully filled.
    """

    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN_ORDER_STATE = "UNKNOWN_ORDER_STATE"
