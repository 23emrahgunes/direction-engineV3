"""Calibrated external-alpha Directional Edge decision logic."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    DecisionAction,
    FeatureVector,
    Market,
    OutcomeSide,
    ProbabilityForecast,
    StrategyCandidate,
    StrategyKind,
)
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import PriceToBeatRecord
from direction_engine_v3.models import CalibrationReadiness
from direction_engine_v3.pricing import DepthSimulation

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class DirectionalPolicy:
    minimum_net_edge: Decimal
    uncertainty_buffer: Decimal
    minimum_signal_stability: Decimal
    maximum_flip_rate: Decimal
    max_forecast_age: timedelta

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_net_edge", self.minimum_net_edge),
            ("uncertainty_buffer", self.uncertainty_buffer),
            ("minimum_signal_stability", self.minimum_signal_stability),
            ("maximum_flip_rate", self.maximum_flip_rate),
        ):
            require_decimal(name, value, minimum=_ZERO, maximum=_ONE)
        if self.max_forecast_age <= timedelta(0):
            raise ValueError("max_forecast_age must be positive")


@dataclass(frozen=True, slots=True)
class DirectionalAssessment:
    action: DecisionAction
    market_id: str
    reason: str
    observed_at: datetime
    selected_side: OutcomeSide | None = None
    selected_probability: Decimal | None = None
    executable_cost: Decimal | None = None
    net_edge: Decimal | None = None
    candidate: StrategyCandidate | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, DecisionAction):
            raise TypeError("action must be DecisionAction")
        require_text("market_id", self.market_id)
        require_text("reason", self.reason)
        require_utc("observed_at", self.observed_at)
        if self.action is DecisionAction.ABSTAIN and self.candidate is not None:
            raise ValueError("ABSTAIN cannot carry a candidate")
        if self.action is DecisionAction.TRADE and self.candidate is None:
            raise ValueError("TRADE requires a candidate")
        if self.selected_side is not None and not isinstance(self.selected_side, OutcomeSide):
            raise TypeError("selected_side must be an OutcomeSide")
        for name, value in (
            ("selected_probability", self.selected_probability),
            ("executable_cost", self.executable_cost),
            ("net_edge", self.net_edge),
        ):
            if value is not None:
                require_decimal(name, value)


def assess_directional_edge(
    market: Market,
    price_to_beat: PriceToBeatRecord | None,
    features: FeatureVector | None,
    forecast: ProbabilityForecast | None,
    calibration: CalibrationReadiness | None,
    up_pricing: DepthSimulation | None,
    down_pricing: DepthSimulation | None,
    *,
    observed_at: datetime,
    policy: DirectionalPolicy,
) -> DirectionalAssessment:
    """Emit a pre-risk candidate only after every strategy-owned gate passes."""

    require_utc("observed_at", observed_at)
    if observed_at < market.window_start or observed_at >= market.window_end:
        return _abstain(market, observed_at, "MARKET_NOT_ACTIVE")
    if price_to_beat is None:
        return _abstain(market, observed_at, "OFFICIAL_PTB_UNAVAILABLE")
    if features is None:
        return _abstain(market, observed_at, "FEATURES_UNAVAILABLE")
    if forecast is None:
        return _abstain(market, observed_at, "MODEL_UNAVAILABLE")
    if calibration is None or not calibration.ready:
        return _abstain(market, observed_at, "CALIBRATION_NOT_READY")
    _require_identity(market, price_to_beat, features, forecast, calibration)
    age = observed_at - forecast.generated_at
    if age < timedelta(0) or age > policy.max_forecast_age:
        return _abstain(market, observed_at, "FORECAST_STALE_OR_FUTURE")
    feature_map = {feature.name: feature.value for feature in features.features}
    stability = feature_map.get("signal_stability")
    flip_rate = feature_map.get("flip_rate")
    if stability is None or flip_rate is None:
        return _abstain(market, observed_at, "SIGNAL_QUALITY_FEATURES_MISSING")
    if stability < policy.minimum_signal_stability:
        return _abstain(market, observed_at, "SIGNAL_UNSTABLE")
    if flip_rate > policy.maximum_flip_rate:
        return _abstain(market, observed_at, "FLIP_RATE_TOO_HIGH")
    if forecast.p_up == forecast.p_down:
        return _abstain(market, observed_at, "NO_DIRECTIONAL_SIDE")
    side = OutcomeSide.UP if forecast.p_up > forecast.p_down else OutcomeSide.DOWN
    probability = forecast.p_up if side is OutcomeSide.UP else forecast.p_down
    pricing = up_pricing if side is OutcomeSide.UP else down_pricing
    if pricing is None or pricing.fill_fraction != _ONE or pricing.all_in_cost_per_share is None:
        return _abstain(market, observed_at, "EXECUTABLE_PRICE_UNAVAILABLE")
    token_by_side = {token.outcome: token.token_id for token in market.tokens}
    if pricing.condition_id != market.condition_id or pricing.token_id != token_by_side[side]:
        return _abstain(market, observed_at, "EXECUTABLE_PRICE_IDENTITY_MISMATCH")
    net_edge = probability - pricing.all_in_cost_per_share - policy.uncertainty_buffer
    if net_edge < policy.minimum_net_edge:
        return DirectionalAssessment(
            DecisionAction.ABSTAIN,
            market.market_id,
            "NET_EDGE_BELOW_MINIMUM",
            observed_at,
            side,
            probability,
            pricing.all_in_cost_per_share,
            net_edge,
        )
    required_capital = pricing.all_in_cost_per_share * pricing.filled_quantity
    candidate = StrategyCandidate(
        candidate_id=f"{market.condition_id}:DIRECTIONAL:{int(observed_at.timestamp() * 1000)}",
        strategy=StrategyKind.DIRECTIONAL_EDGE,
        market_id=market.market_id,
        required_capital=required_capital,
        expected_net_value=net_edge * pricing.filled_quantity,
        quality_score=probability,
        created_at=observed_at,
        expires_at=min(market.window_end, observed_at + policy.max_forecast_age),
        outcome_side=side,
    )
    return DirectionalAssessment(
        DecisionAction.TRADE,
        market.market_id,
        "PRE_RISK_CANDIDATE",
        observed_at,
        side,
        probability,
        pricing.all_in_cost_per_share,
        net_edge,
        candidate,
    )


def _require_identity(
    market: Market,
    ptb: PriceToBeatRecord,
    features: FeatureVector,
    forecast: ProbabilityForecast,
    calibration: CalibrationReadiness,
) -> None:
    if ptb.reference.market_id != market.market_id or ptb.condition_id != market.condition_id:
        raise ValueError("price-to-beat identity mismatch")
    for item_name, item in (("features", features), ("forecast", forecast)):
        if (
            item.market_id != market.market_id
            or item.asset is not market.asset
            or item.horizon is not market.horizon
        ):
            raise ValueError(f"{item_name} identity mismatch")
    if forecast.feature_set_version != features.feature_set_version:
        raise ValueError("forecast feature schema mismatch")
    if calibration.asset is not market.asset or calibration.horizon is not market.horizon:
        raise ValueError("calibration bucket mismatch")
    if calibration.calibration_version != forecast.calibration_version:
        raise ValueError("calibration version mismatch")


def _abstain(market: Market, observed_at: datetime, reason: str) -> DirectionalAssessment:
    return DirectionalAssessment(DecisionAction.ABSTAIN, market.market_id, reason, observed_at)
