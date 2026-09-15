"""Pure depth-aware execution simulation; never submits orders."""

from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import OrderSide
from direction_engine_v3.domain._validation import require_decimal, require_utc
from direction_engine_v3.market_data import FeeSchedule, PolymarketBook, PolymarketLevel
from direction_engine_v3.pricing.contracts import (
    DepthFill,
    DepthSimulation,
    LiquidityRole,
    PricingPolicy,
)
from direction_engine_v3.pricing.fees import (
    PricingUnavailableError,
    quote_fee,
    require_fee_schedule,
)

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def simulate_depth(
    book: PolymarketBook,
    fee_schedule: FeeSchedule,
    *,
    side: OrderSide,
    requested_quantity: Decimal,
    limit_price: Decimal,
    role: LiquidityRole,
    observed_at: datetime,
    policy: PricingPolicy,
) -> DepthSimulation:
    """Consume eligible levels and return truthful partial/no-fill economics."""

    if not isinstance(book, PolymarketBook):
        raise TypeError("book must be a PolymarketBook")
    if not isinstance(side, OrderSide):
        raise TypeError("side must be an OrderSide")
    require_decimal(
        "requested_quantity", requested_quantity, minimum=_ZERO, minimum_exclusive=True
    )
    require_decimal("limit_price", limit_price, minimum=_ZERO, maximum=Decimal("1"))
    require_utc("observed_at", observed_at)
    _require_fresh_book(book, observed_at=observed_at, max_age=policy.max_book_age)
    require_fee_schedule(
        fee_schedule,
        condition_id=book.condition_id,
        observed_at=observed_at,
        max_age=policy.max_fee_age,
    )
    levels = _eligible_levels(book, side=side, limit_price=limit_price)
    executable_depth = sum((level.quantity for level in levels), _ZERO)
    remaining = requested_quantity
    fills: list[DepthFill] = []
    for level in levels:
        if remaining == _ZERO:
            break
        quantity = min(level.quantity, remaining)
        fee = quote_fee(fee_schedule, quantity=quantity, price=level.price, role=role)
        fills.append(
            DepthFill(
                price=level.price,
                quantity=quantity,
                gross_notional=level.price * quantity,
                fee_usdc=fee.fee_usdc,
            )
        )
        remaining -= quantity
    filled = requested_quantity - remaining
    gross = sum((fill.gross_notional for fill in fills), _ZERO)
    total_fee = sum((fill.fee_usdc for fill in fills), _ZERO)
    fee_buffer = total_fee * policy.fee_buffer_bps / _BPS
    slippage_buffer = gross * policy.slippage_buffer_bps / _BPS
    if filled == _ZERO:
        vwap = worst = impact = all_in = net_proceeds = None
    else:
        vwap = gross / filled
        worst = fills[-1].price
        best = fills[0].price
        impact = vwap - best if side is OrderSide.BUY else best - vwap
        if side is OrderSide.BUY:
            all_in = (gross + total_fee + fee_buffer + slippage_buffer) / filled
            net_proceeds = None
        else:
            all_in = None
            net_proceeds = (gross - total_fee - fee_buffer - slippage_buffer) / filled
            if net_proceeds < _ZERO:
                raise PricingUnavailableError("conservative SELL proceeds became negative")
    return DepthSimulation(
        condition_id=book.condition_id,
        token_id=book.token_id,
        side=side,
        requested_quantity=requested_quantity,
        executable_depth=executable_depth,
        filled_quantity=filled,
        fill_fraction=filled / requested_quantity,
        fills=tuple(fills),
        gross_notional=gross,
        total_fee_usdc=total_fee,
        fee_buffer_usdc=fee_buffer,
        slippage_buffer_usdc=slippage_buffer,
        vwap=vwap,
        worst_price=worst,
        depth_impact_per_share=impact,
        all_in_cost_per_share=all_in,
        net_proceeds_per_share=net_proceeds,
        simulated_at=observed_at,
    )


def _eligible_levels(
    book: PolymarketBook, *, side: OrderSide, limit_price: Decimal
) -> tuple[PolymarketLevel, ...]:
    if side is OrderSide.BUY:
        return tuple(level for level in book.asks if level.price <= limit_price)
    return tuple(level for level in book.bids if level.price >= limit_price)


def _require_fresh_book(
    book: PolymarketBook, *, observed_at: datetime, max_age: timedelta
) -> None:
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    if book.lineage.source_ts is None:
        raise PricingUnavailableError("book source timestamp is missing")
    for label, timestamp in (
        ("source", book.lineage.source_ts),
        ("receive", book.lineage.recv_ts),
    ):
        age = observed_at - timestamp
        if age < timedelta(0):
            raise PricingUnavailableError(f"book {label} timestamp is in the future")
        if age > max_age:
            raise PricingUnavailableError(f"book {label} timestamp is stale")
