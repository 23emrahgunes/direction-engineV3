from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import OrderSide
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    PolymarketBook,
    PolymarketLevel,
)
from direction_engine_v3.pricing import (
    FEE_FORMULA_VERSION,
    LiquidityRole,
    PricingPolicy,
    PricingUnavailableError,
    quote_fee,
    require_fee_schedule,
    simulate_depth,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
LINEAGE = EventLineage(
    source=DataSource.POLYMARKET_CLOB,
    source_ts=NOW - timedelta(milliseconds=100),
    recv_ts=NOW - timedelta(milliseconds=50),
    normalized_ts=NOW - timedelta(milliseconds=40),
    recv_monotonic_ns=100,
)
POLICY = PricingPolicy(
    max_book_age=timedelta(seconds=1),
    max_fee_age=timedelta(minutes=5),
    slippage_buffer_bps=Decimal("100"),
    fee_buffer_bps=Decimal("1000"),
)


def fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        condition_id="condition-1",
        maker_base_bps=Decimal("0"),
        taker_base_bps=Decimal("1000"),
        rate=Decimal("0.07"),
        exponent=Decimal("1"),
        lineage=replace(LINEAGE, source_ts=None),
    )


def book() -> PolymarketBook:
    return PolymarketBook(
        condition_id="condition-1",
        token_id="up-token",
        bids=(
            PolymarketLevel(Decimal("0.35"), Decimal("5")),
            PolymarketLevel(Decimal("0.30"), Decimal("10")),
        ),
        asks=(
            PolymarketLevel(Decimal("0.40"), Decimal("5")),
            PolymarketLevel(Decimal("0.50"), Decimal("10")),
        ),
        checksum="book-hash",
        minimum_order_size=Decimal("5"),
        tick_size=Decimal("0.01"),
        lineage=LINEAGE,
    )


def test_dynamic_crypto_taker_fee_matches_documented_formula() -> None:
    quote = quote_fee(
        fee_schedule(),
        quantity=Decimal("100"),
        price=Decimal("0.50"),
        role=LiquidityRole.TAKER,
    )
    assert quote.fee_usdc == Decimal("1.7500")
    assert quote.formula_version == FEE_FORMULA_VERSION
    assert quote.schedule_lineage == fee_schedule().lineage


@pytest.mark.parametrize("price", [Decimal("0.30"), Decimal("0.70")])
def test_dynamic_fee_is_symmetric_around_half(price: Decimal) -> None:
    quote = quote_fee(
        fee_schedule(), quantity=Decimal("100"), price=price, role=LiquidityRole.TAKER
    )
    assert quote.fee_usdc == Decimal("1.4700")


def test_dynamic_fee_uses_documented_five_decimal_precision() -> None:
    quote = quote_fee(
        fee_schedule(),
        quantity=Decimal("0.001"),
        price=Decimal("0.01"),
        role=LiquidityRole.TAKER,
    )
    assert quote.fee_usdc == Decimal("0.00000")


def test_maker_is_zero_only_when_schedule_confirms_zero() -> None:
    quote = quote_fee(
        fee_schedule(),
        quantity=Decimal("100"),
        price=Decimal("0.50"),
        role=LiquidityRole.MAKER,
    )
    assert quote.fee_usdc == 0
    with pytest.raises(PricingUnavailableError, match="maker fee"):
        quote_fee(
            replace(fee_schedule(), maker_base_bps=Decimal("1")),
            quantity=Decimal("100"),
            price=Decimal("0.50"),
            role=LiquidityRole.MAKER,
        )


@pytest.mark.parametrize(
    "schedule",
    [
        replace(fee_schedule(), rate=None),
        replace(fee_schedule(), exponent=None),
        replace(fee_schedule(), exponent=Decimal("1.5")),
    ],
)
def test_missing_or_unsupported_dynamic_fee_parameters_fail_closed(
    schedule: FeeSchedule,
) -> None:
    with pytest.raises(PricingUnavailableError):
        quote_fee(
            schedule,
            quantity=Decimal("1"),
            price=Decimal("0.5"),
            role=LiquidityRole.TAKER,
        )


def test_fee_schedule_requires_matching_recent_condition() -> None:
    require_fee_schedule(
        fee_schedule(),
        condition_id="condition-1",
        observed_at=NOW,
        max_age=timedelta(minutes=1),
    )
    with pytest.raises(PricingUnavailableError, match="identity"):
        require_fee_schedule(
            fee_schedule(),
            condition_id="condition-2",
            observed_at=NOW,
            max_age=timedelta(minutes=1),
        )
    with pytest.raises(PricingUnavailableError, match="stale"):
        require_fee_schedule(
            fee_schedule(),
            condition_id="condition-1",
            observed_at=NOW + timedelta(minutes=6),
            max_age=timedelta(minutes=5),
        )


def test_buy_simulation_consumes_levels_and_includes_all_cost_buffers() -> None:
    result = simulate_depth(
        book(),
        fee_schedule(),
        side=OrderSide.BUY,
        requested_quantity=Decimal("10"),
        limit_price=Decimal("0.50"),
        role=LiquidityRole.TAKER,
        observed_at=NOW,
        policy=POLICY,
    )
    assert [fill.quantity for fill in result.fills] == [Decimal("5"), Decimal("5")]
    assert result.executable_depth == Decimal("15")
    assert result.filled_quantity == Decimal("10")
    assert result.fill_fraction == Decimal("1")
    assert result.gross_notional == Decimal("4.50")
    assert result.vwap == Decimal("0.45")
    assert result.worst_price == Decimal("0.50")
    assert result.depth_impact_per_share == Decimal("0.05")
    assert result.total_fee_usdc == Decimal("0.1715")
    assert result.fee_buffer_usdc == Decimal("0.01715")
    assert result.slippage_buffer_usdc == Decimal("0.045")
    assert result.all_in_cost_per_share == Decimal("0.473365")
    assert result.net_proceeds_per_share is None


def test_sell_simulation_reports_conservative_net_proceeds() -> None:
    result = simulate_depth(
        book(),
        fee_schedule(),
        side=OrderSide.SELL,
        requested_quantity=Decimal("10"),
        limit_price=Decimal("0.30"),
        role=LiquidityRole.TAKER,
        observed_at=NOW,
        policy=POLICY,
    )
    assert result.vwap == Decimal("0.325")
    assert result.depth_impact_per_share == Decimal("0.025")
    assert result.all_in_cost_per_share is None
    assert result.net_proceeds_per_share == Decimal("0.3049057")


def test_insufficient_depth_is_a_truthful_partial_fill() -> None:
    result = simulate_depth(
        book(),
        fee_schedule(),
        side=OrderSide.BUY,
        requested_quantity=Decimal("20"),
        limit_price=Decimal("0.50"),
        role=LiquidityRole.TAKER,
        observed_at=NOW,
        policy=POLICY,
    )
    assert result.executable_depth == Decimal("15")
    assert result.filled_quantity == Decimal("15")
    assert result.fill_fraction == Decimal("0.75")


def test_empty_or_ineligible_depth_is_not_fabricated_as_a_fill() -> None:
    result = simulate_depth(
        book(),
        fee_schedule(),
        side=OrderSide.BUY,
        requested_quantity=Decimal("5"),
        limit_price=Decimal("0.30"),
        role=LiquidityRole.TAKER,
        observed_at=NOW,
        policy=POLICY,
    )
    assert result.filled_quantity == 0
    assert result.fills == ()
    assert result.vwap is None
    assert result.all_in_cost_per_share is None


def test_stale_missing_timestamp_or_wrong_condition_fails_closed() -> None:
    with pytest.raises(PricingUnavailableError, match="source timestamp is stale"):
        simulate_depth(
            book(),
            fee_schedule(),
            side=OrderSide.BUY,
            requested_quantity=Decimal("5"),
            limit_price=Decimal("0.50"),
            role=LiquidityRole.TAKER,
            observed_at=NOW + timedelta(seconds=2),
            policy=POLICY,
        )
    with pytest.raises(PricingUnavailableError, match="source timestamp is missing"):
        simulate_depth(
            replace(book(), lineage=replace(LINEAGE, source_ts=None)),
            fee_schedule(),
            side=OrderSide.BUY,
            requested_quantity=Decimal("5"),
            limit_price=Decimal("0.50"),
            role=LiquidityRole.TAKER,
            observed_at=NOW,
            policy=POLICY,
        )
    with pytest.raises(PricingUnavailableError, match="condition identity"):
        simulate_depth(
            book(),
            replace(fee_schedule(), condition_id="other-condition"),
            side=OrderSide.BUY,
            requested_quantity=Decimal("5"),
            limit_price=Decimal("0.50"),
            role=LiquidityRole.TAKER,
            observed_at=NOW,
            policy=POLICY,
        )
