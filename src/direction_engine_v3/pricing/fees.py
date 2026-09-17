"""Current Polymarket dynamic fee calculation."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from direction_engine_v3.domain._validation import require_decimal, require_utc
from direction_engine_v3.market_data import FeeSchedule
from direction_engine_v3.pricing.contracts import FeeQuote, LiquidityRole

FEE_FORMULA_VERSION: Final = "polymarket-v2-dynamic-fee-v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_FEE_QUANTUM = Decimal("0.00001")


class PricingUnavailableError(RuntimeError):
    """Required book or fee evidence is missing, stale, or unsupported."""


def require_fee_schedule(
    schedule: FeeSchedule,
    *,
    condition_id: str,
    observed_at: datetime,
    max_age: timedelta,
) -> None:
    """Require an identity-matched recent fee schedule."""

    if not isinstance(schedule, FeeSchedule):
        raise TypeError("schedule must be a FeeSchedule")
    require_utc("observed_at", observed_at)
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    if schedule.condition_id != condition_id:
        raise PricingUnavailableError("fee schedule condition identity mismatch")
    age = observed_at - schedule.lineage.recv_ts
    if age < timedelta(0):
        raise PricingUnavailableError("fee schedule receive time is in the future")
    if age > max_age:
        raise PricingUnavailableError("fee schedule is stale")


def quote_fee(
    schedule: FeeSchedule,
    *,
    quantity: Decimal,
    price: Decimal,
    role: LiquidityRole,
) -> FeeQuote:
    """Calculate fee = shares * rate * (price * (1-price)) ** exponent."""

    require_decimal("quantity", quantity, minimum=_ZERO, minimum_exclusive=True)
    require_decimal("price", price, minimum=_ZERO, maximum=_ONE)
    if not isinstance(role, LiquidityRole):
        raise TypeError("role must be a LiquidityRole")
    if role is LiquidityRole.MAKER:
        if schedule.maker_base_bps != _ZERO:
            raise PricingUnavailableError("non-zero maker fee formula is unsupported")
        fee = _ZERO
    else:
        if schedule.taker_fee_mode == "bps":
            raw_fee = quantity * price * schedule.taker_base_bps / Decimal("10000")
        else:
            if schedule.rate is None or schedule.exponent is None:
                raise PricingUnavailableError("dynamic fee parameters are incomplete")
            if schedule.exponent != schedule.exponent.to_integral_value():
                raise PricingUnavailableError("fee exponent must be an integer")
            exponent = int(schedule.exponent)
            raw_fee = quantity * schedule.rate * (price * (_ONE - price)) ** exponent
        fee = raw_fee.quantize(_FEE_QUANTUM, rounding=ROUND_HALF_UP)
    return FeeQuote(
        condition_id=schedule.condition_id,
        formula_version=FEE_FORMULA_VERSION,
        role=role,
        quantity=quantity,
        price=price,
        fee_usdc=fee,
        schedule_lineage=schedule.lineage,
    )
