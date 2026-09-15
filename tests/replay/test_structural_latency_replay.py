from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, Horizon, Market, MarketToken, OutcomeSide
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    PolymarketBook,
    PolymarketLevel,
)
from direction_engine_v3.pricing import PricingPolicy
from direction_engine_v3.replay import (
    SUPPORTED_LATENCIES_MS,
    ReplayBookEvent,
    replay_buy_merge,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
POLICY = PricingPolicy(
    max_book_age=timedelta(seconds=5),
    max_fee_age=timedelta(minutes=1),
    slippage_buffer_bps=Decimal("0"),
    fee_buffer_bps=Decimal("0"),
)


def market() -> Market:
    return Market(
        "market-1",
        "condition-1",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        (MarketToken("up", OutcomeSide.UP), MarketToken("down", OutcomeSide.DOWN)),
        NOW,
        NOW + timedelta(minutes=5),
        "official",
    )


def book(
    token: str,
    *,
    recv_ms: int = 0,
    source_ms: int | None = None,
    ask_qty: str = "5",
    bid_qty: str = "5",
) -> PolymarketBook:
    source = NOW + timedelta(milliseconds=source_ms if source_ms is not None else recv_ms)
    recv = NOW + timedelta(milliseconds=recv_ms)
    asks = () if ask_qty == "0" else (PolymarketLevel(Decimal("0.45"), Decimal(ask_qty)),)
    bids = () if bid_qty == "0" else (PolymarketLevel(Decimal("0.40"), Decimal(bid_qty)),)
    return PolymarketBook(
        "condition-1",
        token,
        bids,
        asks,
        f"{token}-{recv_ms}-{ask_qty}-{bid_qty}",
        Decimal("1"),
        Decimal("0.01"),
        EventLineage(DataSource.POLYMARKET_CLOB, source, recv, recv, recv_ms + 1),
    )


def fee() -> FeeSchedule:
    lineage = EventLineage(DataSource.POLYMARKET_CLOB, None, NOW, NOW, 1)
    return FeeSchedule(
        "condition-1", Decimal("0"), Decimal("1000"), Decimal("0.07"), Decimal("1"), lineage
    )


@pytest.mark.parametrize("latency_ms", SUPPORTED_LATENCIES_MS)
def test_governed_latency_matrix_completes_both_legs(latency_ms: int) -> None:
    events = (
        ReplayBookEvent(OutcomeSide.UP, book("up")),
        ReplayBookEvent(OutcomeSide.DOWN, book("down")),
    )
    result = replay_buy_merge(
        market(),
        events,
        fee(),
        fee(),
        decision_at=NOW,
        latency_ms=latency_ms,
        requested_shares=Decimal("5"),
        pricing_policy=POLICY,
    )
    assert result.both_leg_completion
    assert result.one_leg_exposure == 0
    assert result.matched_complete_sets == Decimal("5")
    assert result.net_cycle_pnl > 0


def test_one_leg_exposure_is_unwound_and_loss_is_recorded() -> None:
    events = (
        ReplayBookEvent(OutcomeSide.UP, book("up")),
        ReplayBookEvent(OutcomeSide.DOWN, book("down", ask_qty="0")),
    )
    result = replay_buy_merge(
        market(),
        events,
        fee(),
        fee(),
        decision_at=NOW,
        latency_ms=100,
        requested_shares=Decimal("5"),
        pricing_policy=POLICY,
    )
    assert not result.both_leg_completion
    assert result.one_leg_exposure == Decimal("5")
    assert result.unwind_feasible
    assert result.unwind_loss is not None and result.unwind_loss > 0
    assert result.net_cycle_pnl < 0


def test_partial_fill_and_infeasible_unwind_are_explicit() -> None:
    events = (
        ReplayBookEvent(OutcomeSide.UP, book("up", ask_qty="3", bid_qty="0")),
        ReplayBookEvent(OutcomeSide.DOWN, book("down", ask_qty="0")),
    )
    result = replay_buy_merge(
        market(),
        events,
        fee(),
        fee(),
        decision_at=NOW,
        latency_ms=25,
        requested_shares=Decimal("5"),
        pricing_policy=POLICY,
    )
    assert result.partial_fill
    assert result.one_leg_exposure == Decimal("3")
    assert not result.unwind_feasible
    assert result.unwind_loss is None


def test_receive_time_as_of_prevents_future_arrival_lookahead() -> None:
    old_empty = book("up", recv_ms=0, ask_qty="0")
    future_arrival = book("up", recv_ms=20, source_ms=-1000, ask_qty="5")
    events = (
        ReplayBookEvent(OutcomeSide.UP, old_empty),
        ReplayBookEvent(OutcomeSide.UP, future_arrival),
        ReplayBookEvent(OutcomeSide.DOWN, book("down")),
    )
    result = replay_buy_merge(
        market(),
        events,
        fee(),
        fee(),
        decision_at=NOW,
        latency_ms=10,
        requested_shares=Decimal("5"),
        pricing_policy=POLICY,
    )
    assert result.up_filled == 0
    assert result.down_filled == Decimal("5")


def test_latency_outside_matrix_is_rejected() -> None:
    with pytest.raises(ValueError, match="latency"):
        replay_buy_merge(
            market(),
            (),
            fee(),
            fee(),
            decision_at=NOW,
            latency_ms=75,
            requested_shares=Decimal("5"),
            pricing_policy=POLICY,
        )
