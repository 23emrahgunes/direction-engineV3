"""Pure fail-closed portfolio and liquidity risk assessment."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    RiskDecision,
    StrategyCandidate,
    StrategyKind,
)
from direction_engine_v3.domain._validation import (
    require_decimal,
    require_text,
    require_tuple,
    require_unique,
    require_utc,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class OpenExposure:
    position_id: str
    market_id: str
    asset: Asset
    horizon: Horizon
    capital_at_risk: Decimal
    window_end: datetime

    def __post_init__(self) -> None:
        require_text("position_id", self.position_id)
        require_text("market_id", self.market_id)
        if not isinstance(self.asset, Asset) or not isinstance(self.horizon, Horizon):
            raise TypeError("asset and horizon must use domain enums")
        require_decimal(
            "capital_at_risk", self.capital_at_risk, minimum=_ZERO, minimum_exclusive=True
        )
        require_utc("window_end", self.window_end)


@dataclass(frozen=True, slots=True)
class LiquidityEvidence:
    observed_at: datetime
    source_age: timedelta
    spread: Decimal
    executable_depth: Decimal
    required_depth: Decimal
    price_impact: Decimal
    persistence_ratio: Decimal
    persistence_observations: int
    sequence_valid: bool
    transport_healthy: bool
    fee_schedule_available: bool

    def __post_init__(self) -> None:
        require_utc("observed_at", self.observed_at)
        if self.source_age < timedelta(0):
            raise ValueError("source_age must be non-negative")
        for name, value in (
            ("spread", self.spread),
            ("executable_depth", self.executable_depth),
            ("required_depth", self.required_depth),
            ("price_impact", self.price_impact),
            ("persistence_ratio", self.persistence_ratio),
        ):
            maximum = _ONE if name in {"spread", "price_impact", "persistence_ratio"} else None
            require_decimal(name, value, minimum=_ZERO, maximum=maximum)
        if self.required_depth <= _ZERO:
            raise ValueError("required_depth must be positive")
        if (
            isinstance(self.persistence_observations, bool)
            or not isinstance(self.persistence_observations, int)
            or self.persistence_observations < 0
        ):
            raise ValueError("persistence_observations must be a non-negative integer")
        for flag in (
            self.sequence_valid,
            self.transport_healthy,
            self.fee_schedule_available,
        ):
            if not isinstance(flag, bool):
                raise TypeError("liquidity state flags must be bools")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    bankroll_available: Decimal | None
    exposures: tuple[OpenExposure, ...] | None
    realized_pnl_today: Decimal | None
    drawdown: Decimal | None
    consecutive_losses: int | None
    cooldown_until: datetime | None
    kill_switch_active: bool
    ledger_reconciled: bool
    assessed_at: datetime

    def __post_init__(self) -> None:
        require_utc("assessed_at", self.assessed_at)
        if self.bankroll_available is not None:
            require_decimal("bankroll_available", self.bankroll_available, minimum=_ZERO)
        if self.exposures is not None:
            require_tuple("exposures", self.exposures)
            if any(not isinstance(item, OpenExposure) for item in self.exposures):
                raise TypeError("exposures must contain OpenExposure values")
            require_unique("position IDs", (item.position_id for item in self.exposures))
        if self.realized_pnl_today is not None:
            require_decimal("realized_pnl_today", self.realized_pnl_today)
        if self.drawdown is not None:
            require_decimal("drawdown", self.drawdown, minimum=_ZERO)
        if self.consecutive_losses is not None and (
            isinstance(self.consecutive_losses, bool)
            or not isinstance(self.consecutive_losses, int)
            or self.consecutive_losses < 0
        ):
            raise ValueError("consecutive_losses must be a non-negative integer")
        if self.cooldown_until is not None:
            require_utc("cooldown_until", self.cooldown_until)
        if not isinstance(self.kill_switch_active, bool) or not isinstance(
            self.ledger_reconciled, bool
        ):
            raise TypeError("portfolio state flags must be bools")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    maximum_positions: int
    maximum_total_exposure: Decimal
    maximum_per_asset_exposure: Decimal
    maximum_per_horizon_exposure: Decimal
    maximum_crypto_cluster_exposure: Decimal
    maximum_overlapping_positions_per_asset: int
    maximum_directional_stake: Decimal
    maximum_daily_loss: Decimal
    maximum_drawdown: Decimal
    consecutive_loss_limit: int
    maximum_state_age: timedelta
    maximum_book_age: timedelta
    maximum_spread: Decimal
    maximum_price_impact: Decimal
    minimum_depth_persistence: Decimal
    minimum_persistence_observations: int

    def __post_init__(self) -> None:
        for name in (
            "maximum_positions",
            "maximum_overlapping_positions_per_asset",
            "consecutive_loss_limit",
            "minimum_persistence_observations",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "maximum_total_exposure",
            "maximum_per_asset_exposure",
            "maximum_per_horizon_exposure",
            "maximum_crypto_cluster_exposure",
            "maximum_directional_stake",
            "maximum_daily_loss",
            "maximum_drawdown",
        ):
            require_decimal(name, getattr(self, name), minimum=_ZERO, minimum_exclusive=True)
        require_decimal("maximum_spread", self.maximum_spread, minimum=_ZERO, maximum=_ONE)
        require_decimal(
            "maximum_price_impact", self.maximum_price_impact, minimum=_ZERO, maximum=_ONE
        )
        require_decimal(
            "minimum_depth_persistence",
            self.minimum_depth_persistence,
            minimum=_ZERO,
            maximum=_ONE,
        )
        if self.maximum_state_age <= timedelta(0) or self.maximum_book_age <= timedelta(0):
            raise ValueError("state and book maximum ages must be positive")


def assess_candidate(
    candidate: StrategyCandidate,
    *,
    asset: Asset,
    horizon: Horizon,
    liquidity: LiquidityEvidence | None,
    state: PortfolioState,
    policy: RiskPolicy,
    assessed_at: datetime,
    risk_decision_id: str,
) -> RiskDecision:
    """Apply all common gates and return every observed denial reason."""

    require_utc("assessed_at", assessed_at)
    require_text("risk_decision_id", risk_decision_id)
    reasons: list[str] = []
    if assessed_at > candidate.expires_at:
        reasons.append("CANDIDATE_EXPIRED")
    state_age = assessed_at - state.assessed_at
    if state_age < timedelta(0) or state_age > policy.maximum_state_age:
        reasons.append("PORTFOLIO_STATE_STALE_OR_FUTURE")
    if state.kill_switch_active:
        reasons.append("GLOBAL_KILL_SWITCH")
    if not state.ledger_reconciled:
        reasons.append("LEDGER_NOT_RECONCILED")
    if (
        state.bankroll_available is None
        or state.exposures is None
        or state.realized_pnl_today is None
        or state.drawdown is None
        or state.consecutive_losses is None
    ):
        reasons.append("PORTFOLIO_STATE_INCOMPLETE")
    else:
        _portfolio_reasons(candidate, asset, horizon, state, policy, assessed_at, reasons)
    _liquidity_reasons(liquidity, policy, assessed_at, reasons)
    if (
        candidate.strategy is StrategyKind.DIRECTIONAL_EDGE
        and candidate.required_capital > policy.maximum_directional_stake
    ):
        reasons.append("DIRECTIONAL_FIXED_STAKE_EXCEEDED")
    if reasons:
        return RiskDecision(
            risk_decision_id,
            candidate.candidate_id,
            False,
            _ZERO,
            assessed_at,
            tuple(dict.fromkeys(reasons)),
        )
    return RiskDecision(
        risk_decision_id,
        candidate.candidate_id,
        True,
        candidate.required_capital,
        assessed_at,
        (),
    )


def _portfolio_reasons(
    candidate: StrategyCandidate,
    asset: Asset,
    horizon: Horizon,
    state: PortfolioState,
    policy: RiskPolicy,
    assessed_at: datetime,
    reasons: list[str],
) -> None:
    assert state.exposures is not None
    assert state.bankroll_available is not None
    assert state.realized_pnl_today is not None
    assert state.drawdown is not None
    assert state.consecutive_losses is not None
    active = tuple(item for item in state.exposures if item.window_end > assessed_at)
    requested = candidate.required_capital
    total = sum((item.capital_at_risk for item in active), _ZERO)
    asset_total = sum((item.capital_at_risk for item in active if item.asset is asset), _ZERO)
    horizon_total = sum(
        (item.capital_at_risk for item in active if item.horizon is horizon), _ZERO
    )
    overlap = sum(item.asset is asset for item in active)
    if len(active) >= policy.maximum_positions:
        reasons.append("MAXIMUM_POSITIONS")
    if requested > state.bankroll_available:
        reasons.append("INSUFFICIENT_BANKROLL")
    if total + requested > policy.maximum_total_exposure:
        reasons.append("TOTAL_EXPOSURE_LIMIT")
    if asset_total + requested > policy.maximum_per_asset_exposure:
        reasons.append("ASSET_EXPOSURE_LIMIT")
    if horizon_total + requested > policy.maximum_per_horizon_exposure:
        reasons.append("HORIZON_EXPOSURE_LIMIT")
    if total + requested > policy.maximum_crypto_cluster_exposure:
        reasons.append("CRYPTO_CLUSTER_EXPOSURE_LIMIT")
    if overlap >= policy.maximum_overlapping_positions_per_asset:
        reasons.append("OVERLAPPING_ASSET_LIMIT")
    if state.realized_pnl_today <= -policy.maximum_daily_loss:
        reasons.append("DAILY_LOSS_LIMIT")
    if state.drawdown >= policy.maximum_drawdown:
        reasons.append("DRAWDOWN_LIMIT")
    if (
        state.consecutive_losses >= policy.consecutive_loss_limit
        and state.cooldown_until is not None
        and assessed_at < state.cooldown_until
    ):
        reasons.append("CONSECUTIVE_LOSS_COOLDOWN_ACTIVE")


def _liquidity_reasons(
    liquidity: LiquidityEvidence | None,
    policy: RiskPolicy,
    assessed_at: datetime,
    reasons: list[str],
) -> None:
    if liquidity is None:
        reasons.append("LIQUIDITY_EVIDENCE_MISSING")
        return
    if liquidity.observed_at > assessed_at or liquidity.source_age > policy.maximum_book_age:
        reasons.append("BOOK_STALE_OR_FUTURE")
    if not liquidity.transport_healthy:
        reasons.append("BOOK_TRANSPORT_UNHEALTHY")
    if not liquidity.sequence_valid:
        reasons.append("BOOK_SEQUENCE_INVALID")
    if not liquidity.fee_schedule_available:
        reasons.append("FEE_SCHEDULE_UNAVAILABLE")
    if liquidity.spread > policy.maximum_spread:
        reasons.append("SPREAD_TOO_WIDE")
    if liquidity.executable_depth < liquidity.required_depth:
        reasons.append("INSUFFICIENT_EXECUTABLE_DEPTH")
    if liquidity.price_impact > policy.maximum_price_impact:
        reasons.append("PRICE_IMPACT_TOO_HIGH")
    if liquidity.persistence_observations < policy.minimum_persistence_observations:
        reasons.append("DEPTH_HISTORY_INSUFFICIENT")
    elif liquidity.persistence_ratio < policy.minimum_depth_persistence:
        reasons.append("TRANSIENT_LIQUIDITY_RISK")
