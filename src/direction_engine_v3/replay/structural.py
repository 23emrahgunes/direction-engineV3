"""Deterministic receive-time replay for two-leg complete-set execution."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import Market, OrderSide, OutcomeSide
from direction_engine_v3.domain._validation import require_decimal, require_utc
from direction_engine_v3.market_data import FeeSchedule, PolymarketBook
from direction_engine_v3.pricing import (
    DepthSimulation,
    LiquidityRole,
    PricingPolicy,
    simulate_depth,
)

SUPPORTED_LATENCIES_MS = (10, 25, 50, 100, 200, 500)
_ZERO = Decimal("0")


class ReplayUnavailableError(RuntimeError):
    """Replay lacks an as-of event required for deterministic execution."""


@dataclass(frozen=True, slots=True)
class ReplayBookEvent:
    outcome: OutcomeSide
    book: PolymarketBook

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, OutcomeSide):
            raise TypeError("outcome must be OutcomeSide")
        if not isinstance(self.book, PolymarketBook):
            raise TypeError("book must be PolymarketBook")


@dataclass(frozen=True, slots=True)
class StructuralReplayResult:
    latency_ms: int
    requested_shares: Decimal
    up_filled: Decimal
    down_filled: Decimal
    matched_complete_sets: Decimal
    one_leg_exposure: Decimal
    both_leg_completion: bool
    partial_fill: bool
    unwind_feasible: bool
    unwind_proceeds: Decimal
    unwind_loss: Decimal | None
    net_cycle_pnl: Decimal
    first_leg_at: datetime
    second_leg_at: datetime

    def __post_init__(self) -> None:
        if self.latency_ms not in SUPPORTED_LATENCIES_MS:
            raise ValueError("latency_ms is not in the governed replay matrix")
        for name, value in (
            ("requested_shares", self.requested_shares),
            ("up_filled", self.up_filled),
            ("down_filled", self.down_filled),
            ("matched_complete_sets", self.matched_complete_sets),
            ("one_leg_exposure", self.one_leg_exposure),
            ("unwind_proceeds", self.unwind_proceeds),
        ):
            require_decimal(name, value, minimum=_ZERO)
        if self.unwind_loss is not None:
            require_decimal("unwind_loss", self.unwind_loss, minimum=_ZERO)
        require_decimal("net_cycle_pnl", self.net_cycle_pnl)
        require_utc("first_leg_at", self.first_leg_at)
        require_utc("second_leg_at", self.second_leg_at)
        if self.second_leg_at <= self.first_leg_at:
            raise ValueError("second leg must follow first leg")


def replay_buy_merge(
    market: Market,
    events: tuple[ReplayBookEvent, ...],
    up_fee: FeeSchedule,
    down_fee: FeeSchedule,
    *,
    decision_at: datetime,
    latency_ms: int,
    requested_shares: Decimal,
    pricing_policy: PricingPolicy,
) -> StructuralReplayResult:
    """Replay sequential BUY legs using receive-time as-of book availability."""

    require_utc("decision_at", decision_at)
    if latency_ms not in SUPPORTED_LATENCIES_MS:
        raise ValueError("latency_ms is not in the governed replay matrix")
    require_decimal(
        "requested_shares", requested_shares, minimum=_ZERO, minimum_exclusive=True
    )
    delay = timedelta(milliseconds=latency_ms)
    first_leg_at = decision_at + delay
    second_leg_at = first_leg_at + delay
    up_book = _as_of(events, OutcomeSide.UP, first_leg_at)
    down_book = _as_of(events, OutcomeSide.DOWN, second_leg_at)
    expected = {token.outcome: token.token_id for token in market.tokens}
    if up_book.condition_id != market.condition_id or down_book.condition_id != market.condition_id:
        raise ReplayUnavailableError("replay condition identity mismatch")
    if (
        up_book.token_id != expected[OutcomeSide.UP]
        or down_book.token_id != expected[OutcomeSide.DOWN]
    ):
        raise ReplayUnavailableError("replay token identity mismatch")
    up = simulate_depth(
        up_book,
        up_fee,
        side=OrderSide.BUY,
        requested_quantity=requested_shares,
        limit_price=Decimal("1"),
        role=LiquidityRole.TAKER,
        observed_at=first_leg_at,
        policy=pricing_policy,
    )
    down = simulate_depth(
        down_book,
        down_fee,
        side=OrderSide.BUY,
        requested_quantity=requested_shares,
        limit_price=Decimal("1"),
        role=LiquidityRole.TAKER,
        observed_at=second_leg_at,
        policy=pricing_policy,
    )
    matched = min(up.filled_quantity, down.filled_quantity)
    exposure = abs(up.filled_quantity - down.filled_quantity)
    up_cost = _buy_cost(up)
    down_cost = _buy_cost(down)
    unwind_proceeds = _ZERO
    unwind_loss: Decimal | None = _ZERO
    unwind_feasible = True
    if exposure > _ZERO:
        excess_side = (
            OutcomeSide.UP if up.filled_quantity > down.filled_quantity else OutcomeSide.DOWN
        )
        excess_buy = up if excess_side is OutcomeSide.UP else down
        unwind_book = _as_of(events, excess_side, second_leg_at)
        unwind_fee = up_fee if excess_side is OutcomeSide.UP else down_fee
        unwind = simulate_depth(
            unwind_book,
            unwind_fee,
            side=OrderSide.SELL,
            requested_quantity=exposure,
            limit_price=_ZERO,
            role=LiquidityRole.TAKER,
            observed_at=second_leg_at,
            policy=pricing_policy,
        )
        unwind_feasible = unwind.filled_quantity == exposure
        if unwind.net_proceeds_per_share is not None:
            unwind_proceeds = unwind.net_proceeds_per_share * unwind.filled_quantity
        if not unwind_feasible or excess_buy.all_in_cost_per_share is None:
            unwind_loss = None
        else:
            excess_cost = excess_buy.all_in_cost_per_share * exposure
            unwind_loss = max(_ZERO, excess_cost - unwind_proceeds)
    net_pnl = matched + unwind_proceeds - up_cost - down_cost
    return StructuralReplayResult(
        latency_ms=latency_ms,
        requested_shares=requested_shares,
        up_filled=up.filled_quantity,
        down_filled=down.filled_quantity,
        matched_complete_sets=matched,
        one_leg_exposure=exposure,
        both_leg_completion=(
            up.filled_quantity == requested_shares
            and down.filled_quantity == requested_shares
        ),
        partial_fill=(
            _ZERO < up.filled_quantity < requested_shares
            or _ZERO < down.filled_quantity < requested_shares
        ),
        unwind_feasible=unwind_feasible,
        unwind_proceeds=unwind_proceeds,
        unwind_loss=unwind_loss,
        net_cycle_pnl=net_pnl,
        first_leg_at=first_leg_at,
        second_leg_at=second_leg_at,
    )


def _as_of(
    events: tuple[ReplayBookEvent, ...], outcome: OutcomeSide, available_at: datetime
) -> PolymarketBook:
    eligible = [
        event.book
        for event in events
        if event.outcome is outcome and event.book.lineage.recv_ts <= available_at
    ]
    if not eligible:
        raise ReplayUnavailableError(f"no {outcome.value} book available by receive time")
    return max(eligible, key=lambda book: book.lineage.recv_ts)


def _buy_cost(simulation: DepthSimulation) -> Decimal:
    return (
        simulation.gross_notional
        + simulation.total_fee_usdc
        + simulation.fee_buffer_usdc
        + simulation.slippage_buffer_usdc
    )
