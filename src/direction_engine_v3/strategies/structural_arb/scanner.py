"""Model-free complete-set structural arbitrage scanner."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from direction_engine_v3.domain import Market, OrderSide, OutcomeSide
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import FeeSchedule, PolymarketBook
from direction_engine_v3.pricing import (
    DepthSimulation,
    LiquidityRole,
    PricingPolicy,
    simulate_depth,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS = Decimal("10000")


class StructuralAction(StrEnum):
    BUY_MERGE = "BUY_MERGE"
    SPLIT_SELL = "SPLIT_SELL"


@dataclass(frozen=True, slots=True)
class StructuralPolicy:
    target_shares: Decimal
    minimum_net_profit: Decimal
    cycle_buffer_bps: Decimal
    max_pair_skew: timedelta
    pricing: PricingPolicy

    def __post_init__(self) -> None:
        require_decimal(
            "target_shares", self.target_shares, minimum=_ZERO, minimum_exclusive=True
        )
        require_decimal("minimum_net_profit", self.minimum_net_profit, minimum=_ZERO)
        require_decimal("cycle_buffer_bps", self.cycle_buffer_bps, minimum=_ZERO)
        if self.max_pair_skew < timedelta(0):
            raise ValueError("max_pair_skew must be non-negative")
        if not isinstance(self.pricing, PricingPolicy):
            raise TypeError("pricing must be a PricingPolicy")


@dataclass(frozen=True, slots=True)
class StructuralOpportunity:
    opportunity_id: str
    action: StructuralAction
    market_id: str
    condition_id: str
    shares: Decimal
    up: DepthSimulation
    down: DepthSimulation
    combined_price_per_share: Decimal
    cycle_buffer_usdc: Decimal
    expected_net_profit: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        require_text("opportunity_id", self.opportunity_id)
        if not isinstance(self.action, StructuralAction):
            raise TypeError("action must be a StructuralAction")
        require_text("market_id", self.market_id)
        require_text("condition_id", self.condition_id)
        require_decimal("shares", self.shares, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("combined_price_per_share", self.combined_price_per_share)
        require_decimal("cycle_buffer_usdc", self.cycle_buffer_usdc, minimum=_ZERO)
        require_decimal("expected_net_profit", self.expected_net_profit)
        require_utc("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class StructuralScan:
    action: StructuralAction
    market_id: str
    observed_at: datetime
    reason: str
    opportunity: StructuralOpportunity | None

    def __post_init__(self) -> None:
        if not isinstance(self.action, StructuralAction):
            raise TypeError("action must be a StructuralAction")
        require_text("market_id", self.market_id)
        require_utc("observed_at", self.observed_at)
        require_text("reason", self.reason)
        if self.opportunity is not None and self.opportunity.market_id != self.market_id:
            raise ValueError("opportunity market identity mismatch")


def scan_complete_set(
    market: Market,
    up_book: PolymarketBook,
    down_book: PolymarketBook,
    up_fee: FeeSchedule,
    down_fee: FeeSchedule,
    *,
    action: StructuralAction,
    observed_at: datetime,
    policy: StructuralPolicy,
) -> StructuralScan:
    """Evaluate parity from executable levels only; no forecasting model is consumed."""

    require_utc("observed_at", observed_at)
    if not isinstance(action, StructuralAction):
        raise TypeError("action must be a StructuralAction")
    if not isinstance(policy, StructuralPolicy):
        raise TypeError("policy must be a StructuralPolicy")
    _require_pair_identity(market, up_book, down_book)
    _require_synchronized(up_book, down_book, policy.max_pair_skew)
    side = OrderSide.BUY if action is StructuralAction.BUY_MERGE else OrderSide.SELL
    limit = _ONE if side is OrderSide.BUY else _ZERO
    up = simulate_depth(
        up_book,
        up_fee,
        side=side,
        requested_quantity=policy.target_shares,
        limit_price=limit,
        role=LiquidityRole.TAKER,
        observed_at=observed_at,
        policy=policy.pricing,
    )
    down = simulate_depth(
        down_book,
        down_fee,
        side=side,
        requested_quantity=policy.target_shares,
        limit_price=limit,
        role=LiquidityRole.TAKER,
        observed_at=observed_at,
        policy=policy.pricing,
    )
    if up.fill_fraction != _ONE or down.fill_fraction != _ONE:
        return StructuralScan(
            action, market.market_id, observed_at, "INSUFFICIENT_PAIRED_DEPTH", None
        )
    if action is StructuralAction.BUY_MERGE:
        if up.all_in_cost_per_share is None or down.all_in_cost_per_share is None:
            raise RuntimeError("BUY simulation omitted all-in cost")
        combined = up.all_in_cost_per_share + down.all_in_cost_per_share
        gross_profit = policy.target_shares * (_ONE - combined)
    else:
        if up.net_proceeds_per_share is None or down.net_proceeds_per_share is None:
            raise RuntimeError("SELL simulation omitted net proceeds")
        combined = up.net_proceeds_per_share + down.net_proceeds_per_share
        gross_profit = policy.target_shares * (combined - _ONE)
    cycle_buffer = policy.target_shares * policy.cycle_buffer_bps / _BPS
    net_profit = gross_profit - cycle_buffer
    if net_profit < policy.minimum_net_profit:
        return StructuralScan(
            action, market.market_id, observed_at, "NET_ECONOMICS_BELOW_MINIMUM", None
        )
    identity = f"{market.condition_id}:{action.value}:{int(observed_at.timestamp() * 1000)}"
    opportunity = StructuralOpportunity(
        opportunity_id=identity,
        action=action,
        market_id=market.market_id,
        condition_id=market.condition_id,
        shares=policy.target_shares,
        up=up,
        down=down,
        combined_price_per_share=combined,
        cycle_buffer_usdc=cycle_buffer,
        expected_net_profit=net_profit,
        observed_at=observed_at,
    )
    return StructuralScan(action, market.market_id, observed_at, "EXECUTABLE", opportunity)


def _require_pair_identity(
    market: Market, up_book: PolymarketBook, down_book: PolymarketBook
) -> None:
    expected = {token.outcome: token.token_id for token in market.tokens}
    if up_book.condition_id != market.condition_id or down_book.condition_id != market.condition_id:
        raise ValueError("book condition identity mismatch")
    if up_book.token_id != expected[OutcomeSide.UP]:
        raise ValueError("UP book token identity mismatch")
    if down_book.token_id != expected[OutcomeSide.DOWN]:
        raise ValueError("DOWN book token identity mismatch")


def _require_synchronized(
    up_book: PolymarketBook, down_book: PolymarketBook, max_pair_skew: timedelta
) -> None:
    up_ts = up_book.lineage.source_ts
    down_ts = down_book.lineage.source_ts
    if up_ts is None or down_ts is None:
        raise ValueError("paired books require source timestamps")
    if abs(up_ts - down_ts) > max_pair_skew:
        raise ValueError("paired books exceed maximum source-time skew")
    if abs(up_book.lineage.recv_ts - down_book.lineage.recv_ts) > max_pair_skew:
        raise ValueError("paired books exceed maximum receive-time skew")
