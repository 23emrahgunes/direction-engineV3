from dataclasses import replace
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
from direction_engine_v3.strategies.structural_arb import (
    OpportunityLifetimeTracker,
    StructuralAction,
    StructuralPolicy,
    scan_complete_set,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PRICING = PricingPolicy(
    max_book_age=timedelta(seconds=2),
    max_fee_age=timedelta(minutes=1),
    slippage_buffer_bps=Decimal("10"),
    fee_buffer_bps=Decimal("500"),
)
POLICY = StructuralPolicy(
    target_shares=Decimal("5"),
    minimum_net_profit=Decimal("0.01"),
    cycle_buffer_bps=Decimal("10"),
    max_pair_skew=timedelta(milliseconds=100),
    pricing=PRICING,
)


def market() -> Market:
    return Market(
        market_id="market-1",
        condition_id="condition-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        tokens=(
            MarketToken("up-token", OutcomeSide.UP),
            MarketToken("down-token", OutcomeSide.DOWN),
        ),
        window_start=NOW,
        window_end=NOW + timedelta(minutes=5),
        settlement_source="official",
    )


def lineage(*, offset_ms: int = 0) -> EventLineage:
    timestamp = NOW + timedelta(milliseconds=offset_ms)
    return EventLineage(
        source=DataSource.POLYMARKET_CLOB,
        source_ts=timestamp,
        recv_ts=timestamp,
        normalized_ts=timestamp,
        recv_monotonic_ns=offset_ms + 100,
    )


def book(
    token_id: str,
    *,
    bid: str = "0.55",
    ask: str = "0.45",
    quantity: str = "10",
    offset_ms: int = 0,
) -> PolymarketBook:
    return PolymarketBook(
        condition_id="condition-1",
        token_id=token_id,
        bids=(PolymarketLevel(Decimal(bid), Decimal(quantity)),),
        asks=(PolymarketLevel(Decimal(ask), Decimal(quantity)),),
        checksum=f"{token_id}-{offset_ms}",
        minimum_order_size=Decimal("5"),
        tick_size=Decimal("0.01"),
        lineage=lineage(offset_ms=offset_ms),
    )


def fee() -> FeeSchedule:
    return FeeSchedule(
        condition_id="condition-1",
        maker_base_bps=Decimal("0"),
        taker_base_bps=Decimal("1000"),
        rate=Decimal("0.07"),
        exponent=Decimal("1"),
        lineage=replace(lineage(), source_ts=None),
    )


def test_buy_merge_uses_equal_full_depth_and_dynamic_costs() -> None:
    scan = scan_complete_set(
        market(),
        book("up-token", bid="0.40"),
        book("down-token", bid="0.40"),
        fee(),
        fee(),
        action=StructuralAction.BUY_MERGE,
        observed_at=NOW + timedelta(milliseconds=50),
        policy=POLICY,
    )
    assert scan.reason == "EXECUTABLE"
    assert scan.opportunity is not None
    assert scan.opportunity.shares == Decimal("5")
    assert scan.opportunity.combined_price_per_share < Decimal("1")
    assert scan.opportunity.expected_net_profit > 0


def test_split_sell_is_model_free_complete_set_economics() -> None:
    scan = scan_complete_set(
        market(),
        book("up-token", bid="0.55", ask="0.60"),
        book("down-token", bid="0.55", ask="0.60"),
        fee(),
        fee(),
        action=StructuralAction.SPLIT_SELL,
        observed_at=NOW + timedelta(milliseconds=50),
        policy=POLICY,
    )
    assert scan.opportunity is not None
    assert scan.opportunity.combined_price_per_share > Decimal("1")
    assert scan.opportunity.expected_net_profit > 0


def test_insufficient_paired_depth_and_bad_economics_abstain() -> None:
    shallow = book("up-token", quantity="2", bid="0.40")
    scan = scan_complete_set(
        market(),
        shallow,
        book("down-token", bid="0.40"),
        fee(),
        fee(),
        action=StructuralAction.BUY_MERGE,
        observed_at=NOW,
        policy=POLICY,
    )
    assert scan.opportunity is None
    assert scan.reason == "INSUFFICIENT_PAIRED_DEPTH"

    expensive = book("up-token", bid="0.40", ask="0.60")
    expensive_down = book("down-token", bid="0.40", ask="0.60")
    scan = scan_complete_set(
        market(),
        expensive,
        expensive_down,
        fee(),
        fee(),
        action=StructuralAction.BUY_MERGE,
        observed_at=NOW,
        policy=POLICY,
    )
    assert scan.opportunity is None
    assert scan.reason == "NET_ECONOMICS_BELOW_MINIMUM"


def test_identity_and_pair_skew_fail_closed() -> None:
    with pytest.raises(ValueError, match="UP book token"):
        scan_complete_set(
            market(),
            book("wrong-token", bid="0.40"),
            book("down-token", bid="0.40"),
            fee(),
            fee(),
            action=StructuralAction.BUY_MERGE,
            observed_at=NOW,
            policy=POLICY,
        )
    with pytest.raises(ValueError, match="skew"):
        scan_complete_set(
            market(),
            book("up-token", bid="0.40"),
            book("down-token", bid="0.40", offset_ms=101),
            fee(),
            fee(),
            action=StructuralAction.BUY_MERGE,
            observed_at=NOW + timedelta(milliseconds=101),
            policy=POLICY,
        )


def test_opportunity_lifetime_tracks_first_last_peak_and_close() -> None:
    scan = scan_complete_set(
        market(),
        book("up-token", bid="0.40"),
        book("down-token", bid="0.40"),
        fee(),
        fee(),
        action=StructuralAction.BUY_MERGE,
        observed_at=NOW + timedelta(milliseconds=50),
        policy=POLICY,
    )
    assert scan.opportunity is not None
    tracker = OpportunityLifetimeTracker()
    first = tracker.observe(scan.opportunity)
    improved = replace(
        scan.opportunity,
        expected_net_profit=scan.opportunity.expected_net_profit + Decimal("1"),
        observed_at=NOW + timedelta(milliseconds=100),
    )
    second = tracker.observe(improved)
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at == improved.observed_at
    assert second.peak_net_profit == improved.expected_net_profit
    assert second.observations == 2
    closed = tracker.close(NOW + timedelta(milliseconds=150))
    assert closed is not None and closed.closed_at is not None
