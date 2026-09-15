"""Closed-enum tests for domain scope and lifecycle semantics."""

import pytest

from direction_engine_v3.domain import (
    Asset,
    DecisionAction,
    Horizon,
    OrderSide,
    OrderStatus,
    OutcomeSide,
    ReferenceKind,
    StrategyKind,
    TimeInForce,
    TradingMode,
)


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (Asset, ("BTC", "ETH", "SOL", "XRP")),
        (Horizon, ("5m", "15m", "1h")),
        (OutcomeSide, ("UP", "DOWN")),
        (ReferenceKind, ("OFFICIAL", "PROXY")),
        (
            StrategyKind,
            ("DIRECTIONAL_EDGE", "STRUCTURAL_ARBITRAGE", "DUAL40"),
        ),
        (DecisionAction, ("TRADE", "ABSTAIN")),
        (TradingMode, ("PAPER", "LIVE")),
        (OrderSide, ("BUY", "SELL")),
        (TimeInForce, ("GTC", "GTD", "FOK", "FAK")),
        (
            OrderStatus,
            (
                "CREATED",
                "SUBMITTING",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
                "EXPIRED",
                "UNKNOWN_ORDER_STATE",
            ),
        ),
    ],
)
def test_enum_values_are_closed_and_exact(
    enum_type: type[Asset]
    | type[Horizon]
    | type[OutcomeSide]
    | type[ReferenceKind]
    | type[StrategyKind]
    | type[DecisionAction]
    | type[TradingMode]
    | type[OrderSide]
    | type[TimeInForce]
    | type[OrderStatus],
    expected_values: tuple[str, ...],
) -> None:
    assert tuple(member.value for member in enum_type) == expected_values


@pytest.mark.parametrize(
    ("enum_type", "unsupported"),
    [
        (Asset, "DOGE"),
        (Horizon, "4h"),
        (OutcomeSide, "YES"),
    ],
)
def test_scope_enums_reject_unsupported_values(
    enum_type: type[Asset] | type[Horizon] | type[OutcomeSide],
    unsupported: str,
) -> None:
    with pytest.raises(ValueError):
        enum_type(unsupported)


def test_acknowledged_and_filled_are_distinct_states() -> None:
    assert OrderStatus.ACKNOWLEDGED is not OrderStatus.FILLED
    assert OrderStatus.ACKNOWLEDGED.value != OrderStatus.FILLED.value
