"""Immutable pricing and depth-simulation results."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from direction_engine_v3.domain import OrderSide
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import EventLineage

_ZERO = Decimal("0")
_ONE = Decimal("1")


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    """Data-age and conservative cost-buffer requirements."""

    max_book_age: timedelta
    max_fee_age: timedelta
    slippage_buffer_bps: Decimal
    fee_buffer_bps: Decimal

    def __post_init__(self) -> None:
        if self.max_book_age <= timedelta(0):
            raise ValueError("max_book_age must be positive")
        if self.max_fee_age <= timedelta(0):
            raise ValueError("max_fee_age must be positive")
        require_decimal("slippage_buffer_bps", self.slippage_buffer_bps, minimum=_ZERO)
        require_decimal("fee_buffer_bps", self.fee_buffer_bps, minimum=_ZERO)


@dataclass(frozen=True, slots=True)
class FeeQuote:
    """One dynamic fee calculation with source lineage."""

    condition_id: str
    formula_version: str
    role: LiquidityRole
    quantity: Decimal
    price: Decimal
    fee_usdc: Decimal
    schedule_lineage: EventLineage

    def __post_init__(self) -> None:
        require_text("condition_id", self.condition_id)
        require_text("formula_version", self.formula_version)
        if not isinstance(self.role, LiquidityRole):
            raise TypeError("role must be a LiquidityRole")
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("price", self.price, minimum=_ZERO, maximum=_ONE)
        require_decimal("fee_usdc", self.fee_usdc, minimum=_ZERO)
        if not isinstance(self.schedule_lineage, EventLineage):
            raise TypeError("schedule_lineage must be EventLineage")


@dataclass(frozen=True, slots=True)
class DepthFill:
    """One simulated book-level fill; it has no venue side effects."""

    price: Decimal
    quantity: Decimal
    gross_notional: Decimal
    fee_usdc: Decimal

    def __post_init__(self) -> None:
        require_decimal("price", self.price, minimum=_ZERO, maximum=_ONE)
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("gross_notional", self.gross_notional, minimum=_ZERO)
        require_decimal("fee_usdc", self.fee_usdc, minimum=_ZERO)
        if self.gross_notional != self.price * self.quantity:
            raise ValueError("gross_notional must equal price times quantity")


@dataclass(frozen=True, slots=True)
class DepthSimulation:
    """Auditable execution estimate from actual CLOB levels."""

    condition_id: str
    token_id: str
    side: OrderSide
    requested_quantity: Decimal
    executable_depth: Decimal
    filled_quantity: Decimal
    fill_fraction: Decimal
    fills: tuple[DepthFill, ...]
    gross_notional: Decimal
    total_fee_usdc: Decimal
    fee_buffer_usdc: Decimal
    slippage_buffer_usdc: Decimal
    vwap: Decimal | None
    worst_price: Decimal | None
    depth_impact_per_share: Decimal | None
    all_in_cost_per_share: Decimal | None
    net_proceeds_per_share: Decimal | None
    simulated_at: datetime

    def __post_init__(self) -> None:
        require_text("condition_id", self.condition_id)
        require_text("token_id", self.token_id)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide")
        for name, value in (
            ("executable_depth", self.executable_depth),
            ("filled_quantity", self.filled_quantity),
            ("gross_notional", self.gross_notional),
            ("total_fee_usdc", self.total_fee_usdc),
            ("fee_buffer_usdc", self.fee_buffer_usdc),
            ("slippage_buffer_usdc", self.slippage_buffer_usdc),
        ):
            require_decimal(name, value, minimum=_ZERO)
        require_decimal(
            "requested_quantity",
            self.requested_quantity,
            minimum=_ZERO,
            minimum_exclusive=True,
        )
        require_decimal("fill_fraction", self.fill_fraction, minimum=_ZERO, maximum=_ONE)
        require_utc("simulated_at", self.simulated_at)
        if not isinstance(self.fills, tuple) or any(
            not isinstance(fill, DepthFill) for fill in self.fills
        ):
            raise TypeError("fills must be a tuple of DepthFill values")
        expected_fraction = (
            self.filled_quantity / self.requested_quantity
            if self.requested_quantity > _ZERO
            else _ZERO
        )
        if self.fill_fraction != expected_fraction:
            raise ValueError("fill_fraction does not match requested and filled quantity")
        if sum((fill.quantity for fill in self.fills), _ZERO) != self.filled_quantity:
            raise ValueError("fill quantities do not match filled_quantity")
        if sum((fill.gross_notional for fill in self.fills), _ZERO) != self.gross_notional:
            raise ValueError("fill notionals do not match gross_notional")
        if sum((fill.fee_usdc for fill in self.fills), _ZERO) != self.total_fee_usdc:
            raise ValueError("fill fees do not match total_fee_usdc")
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity cannot exceed requested_quantity")
        if self.filled_quantity > self.executable_depth:
            raise ValueError("filled_quantity cannot exceed executable_depth")
        if self.filled_quantity == _ZERO:
            if any(
                value is not None
                for value in (
                    self.vwap,
                    self.worst_price,
                    self.depth_impact_per_share,
                    self.all_in_cost_per_share,
                    self.net_proceeds_per_share,
                )
            ):
                raise ValueError("an unfilled simulation cannot have per-share results")
        else:
            for name, optional_value in (
                ("vwap", self.vwap),
                ("worst_price", self.worst_price),
                ("depth_impact_per_share", self.depth_impact_per_share),
            ):
                if optional_value is None:
                    raise ValueError(f"filled simulation requires {name}")
                require_decimal(name, optional_value, minimum=_ZERO)
        if self.side is OrderSide.BUY:
            if self.net_proceeds_per_share is not None:
                raise ValueError("BUY simulation cannot have net proceeds")
            if self.filled_quantity and self.all_in_cost_per_share is None:
                raise ValueError("filled BUY simulation requires all-in cost")
        if self.side is OrderSide.SELL:
            if self.all_in_cost_per_share is not None:
                raise ValueError("SELL simulation cannot have all-in cost")
            if self.filled_quantity and self.net_proceeds_per_share is None:
                raise ValueError("filled SELL simulation requires net proceeds")
