"""Calibrated external-alpha Directional Edge decision logic."""

from collections.abc import Mapping
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


AUDIT_SCHEMA_VERSION = "DIRECTIONAL_DECISION_AUDIT_V1"

_GATE_ORDER = (
    "MARKET_GATE",
    "PTB_GATE",
    "FEATURE_GATE",
    "MODEL_GATE",
    "CALIBRATION_GATE",
    "IDENTITY_GATE",
    "FORECAST_FRESHNESS_GATE",
    "SIGNAL_STABILITY_GATE",
    "FLIP_RATE_GATE",
    "DIRECTION_GATE",
    "PRICING_GATE",
    "PRICING_IDENTITY_GATE",
    "NET_EDGE_GATE",
    "RISK_GATE",
    "EXECUTION_GATE",
)

_REASON_TO_GATE = {
    "MARKET_NOT_ACTIVE": "MARKET_GATE",
    "OFFICIAL_PTB_UNAVAILABLE": "PTB_GATE",
    "FEATURES_UNAVAILABLE": "FEATURE_GATE",
    "MODEL_UNAVAILABLE": "MODEL_GATE",
    "CALIBRATION_NOT_READY": "CALIBRATION_GATE",
    "FORECAST_STALE_OR_FUTURE": "FORECAST_FRESHNESS_GATE",
    "SIGNAL_QUALITY_FEATURES_MISSING": "SIGNAL_STABILITY_GATE",
    "SIGNAL_UNSTABLE": "SIGNAL_STABILITY_GATE",
    "FLIP_RATE_TOO_HIGH": "FLIP_RATE_GATE",
    "NO_DIRECTIONAL_SIDE": "DIRECTION_GATE",
    "EXECUTABLE_PRICE_UNAVAILABLE": "PRICING_GATE",
    "EXECUTABLE_PRICE_IDENTITY_MISMATCH": "PRICING_IDENTITY_GATE",
    "NET_EDGE_BELOW_MINIMUM": "NET_EDGE_GATE",
}


def build_directional_decision_audit(
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
    assessment: DirectionalAssessment,
    temporal_diagnostics: Mapping[str, object] | None = None,
    pricing_status: str | None = None,
    directional_execution: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a compact read-only audit trace without changing strategy behavior."""

    require_utc("observed_at", observed_at)
    first_failing_gate = _REASON_TO_GATE.get(assessment.reason)
    gates = _gate_statuses(first_failing_gate, assessment.action)
    if directional_execution:
        risk_approved = directional_execution.get("risk_approved")
        if risk_approved is True:
            gates["RISK_GATE"]["status"] = "PASS"
            gates["EXECUTION_GATE"]["status"] = (
                "PASS" if directional_execution.get("paper_fill_status") else "NOT_REACHED"
            )
        elif risk_approved is False:
            gates["RISK_GATE"]["status"] = "FAIL"
            gates["EXECUTION_GATE"]["status"] = "NOT_REACHED"
        gates["RISK_GATE"]["details"] = {
            "risk_approved": risk_approved,
            "risk_reasons": directional_execution.get("risk_reasons", ()),
            "risk_brake_active": directional_execution.get("risk_brake_active"),
            "risk_brake_reason": directional_execution.get("risk_brake_reason"),
        }

    feature_values = _feature_values(features)
    signal = _signal_audit(feature_values, policy, temporal_diagnostics)
    pricing = _pricing_audit(market, forecast, assessment, up_pricing, down_pricing, policy)
    edge = _edge_audit(assessment, policy, pricing)
    model = {
        "model_state": None if forecast is None else "FORECAST_READY",
        "model_version": None if forecast is None else forecast.model_version,
        "calibration_state": (
            "CALIBRATION_READY"
            if calibration is not None and calibration.ready
            else "CALIBRATION_NOT_READY"
        ),
        "calibration_version": None if calibration is None else calibration.calibration_version,
        "feature_set_version": None if features is None else features.feature_set_version,
        "p_up": None if forecast is None else str(forecast.p_up),
        "p_down": None if forecast is None else str(forecast.p_down),
        "selected_side": (
            None if assessment.selected_side is None else assessment.selected_side.value
        ),
        "selected_probability": (
            None
            if assessment.selected_probability is None
            else str(assessment.selected_probability)
        ),
        "probability_margin_from_50": (
            None
            if assessment.selected_probability is None
            else str(abs(assessment.selected_probability - Decimal("0.50")))
        ),
        "probability_margin_from_50_status": "diagnostic_only",
    }
    return {
        "version": AUDIT_SCHEMA_VERSION,
        "final_decision": assessment.action.value,
        "final_reason": assessment.reason,
        "first_failing_gate": first_failing_gate,
        "gates": gates,
        "market": {
            "market_id": market.market_id,
            "condition_id": market.condition_id,
            "asset": market.asset.value,
            "horizon": market.horizon.value,
            "window_start": market.window_start.isoformat(),
            "window_end": market.window_end.isoformat(),
        },
        "ptb": {
            "available": price_to_beat is not None,
            "value": None if price_to_beat is None else str(price_to_beat.value),
            "effective_ts": (
                None if price_to_beat is None else price_to_beat.reference.effective_ts.isoformat()
            ),
        },
        "signal": signal,
        "temporal_state": dict(temporal_diagnostics or {}),
        "model": model,
        "features": {
            name: (None if value is None else str(value)) for name, value in feature_values.items()
        },
        "pricing": pricing | {"pricing_status": pricing_status},
        "edge": edge,
        "real_order_submission": False,
    }


def _gate_statuses(
    first_failing_gate: str | None, action: DecisionAction
) -> dict[str, dict[str, object]]:
    gates: dict[str, dict[str, object]] = {}
    failed = False
    for gate in _GATE_ORDER:
        if first_failing_gate is None:
            status = "PASS" if gate not in {"RISK_GATE", "EXECUTION_GATE"} else "NOT_REACHED"
        elif failed:
            status = "NOT_REACHED"
        elif gate == first_failing_gate:
            status = "FAIL"
            failed = True
        else:
            status = "PASS"
        gates[gate] = {"status": status}
    if action is DecisionAction.TRADE:
        gates["NET_EDGE_GATE"]["status"] = "PASS"
    return gates


def _feature_values(features: FeatureVector | None) -> dict[str, Decimal | None]:
    names = (
        "ptb_normalized_distance",
        "short_return",
        "medium_return",
        "momentum",
        "realized_volatility",
        "volatility_acceleration",
        "spot_perp_basis",
        "external_book_imbalance",
        "microprice_distance",
        "trade_imbalance",
        "signal_stability",
        "flip_rate",
        "regime_score",
    )
    values: dict[str, Decimal | None] = {name: None for name in names}
    if features is None:
        return values
    values.update({item.name: item.value for item in features.features if item.name in values})
    return values


def _signal_audit(
    feature_values: Mapping[str, Decimal | None],
    policy: DirectionalPolicy,
    temporal_diagnostics: Mapping[str, object] | None,
) -> dict[str, object]:
    stability = feature_values.get("signal_stability")
    flip_rate = feature_values.get("flip_rate")
    diagnostics = temporal_diagnostics or {}
    return {
        "signal_stability_formula": "1 - flip_rate",
        "signal_stability_actual": None if stability is None else str(stability),
        "signal_stability_minimum": str(policy.minimum_signal_stability),
        "signal_stability_minus_minimum": (
            None if stability is None else str(stability - policy.minimum_signal_stability)
        ),
        "flip_rate_actual": None if flip_rate is None else str(flip_rate),
        "flip_rate_maximum": str(policy.maximum_flip_rate),
        "maximum_flip_rate_minus_actual": (
            None if flip_rate is None else str(policy.maximum_flip_rate - flip_rate)
        ),
        "flip_count": diagnostics.get("flip_count"),
        "flip_denominator": diagnostics.get("flip_denominator"),
        "return_sample_count": diagnostics.get("return_sample_count"),
        "return_sign_count": diagnostics.get("return_sign_count"),
        "positive_return_sign_count": diagnostics.get("positive_return_sign_count"),
        "negative_return_sign_count": diagnostics.get("negative_return_sign_count"),
    }


def _pricing_audit(
    market: Market,
    forecast: ProbabilityForecast | None,
    assessment: DirectionalAssessment,
    up_pricing: DepthSimulation | None,
    down_pricing: DepthSimulation | None,
    policy: DirectionalPolicy,
) -> dict[str, object]:
    selected_pricing = None
    if assessment.selected_side is OutcomeSide.UP:
        selected_pricing = up_pricing
    elif assessment.selected_side is OutcomeSide.DOWN:
        selected_pricing = down_pricing
    selected_cost = None if selected_pricing is None else selected_pricing.all_in_cost_per_share
    selected_probability = assessment.selected_probability
    raw_edge = (
        None
        if selected_probability is None or selected_cost is None
        else selected_probability - selected_cost
    )
    counterfactual_net_edge = None if raw_edge is None else raw_edge - policy.uncertainty_buffer
    token_by_side = {token.outcome: token.token_id for token in market.tokens}
    return {
        "counterfactual_pricing_available": up_pricing is not None or down_pricing is not None,
        "up_all_in_cost": _pricing_cost(up_pricing),
        "down_all_in_cost": _pricing_cost(down_pricing),
        "up_fill_fraction": _pricing_attr(up_pricing, "fill_fraction"),
        "down_fill_fraction": _pricing_attr(down_pricing, "fill_fraction"),
        "up_filled_quantity": _pricing_attr(up_pricing, "filled_quantity"),
        "down_filled_quantity": _pricing_attr(down_pricing, "filled_quantity"),
        "selected_side_cost": None if selected_cost is None else str(selected_cost),
        "selected_token_id": None
        if assessment.selected_side is None
        else token_by_side[assessment.selected_side],
        "raw_edge": None if raw_edge is None else str(raw_edge),
        "uncertainty_buffer": str(policy.uncertainty_buffer),
        "counterfactual_net_edge": (
            None if counterfactual_net_edge is None else str(counterfactual_net_edge)
        ),
        "minimum_net_edge": str(policy.minimum_net_edge),
        "counterfactual_edge_margin": (
            None
            if counterfactual_net_edge is None
            else str(counterfactual_net_edge - policy.minimum_net_edge)
        ),
    }


def _edge_audit(
    assessment: DirectionalAssessment,
    policy: DirectionalPolicy,
    pricing: Mapping[str, object],
) -> dict[str, object]:
    net_edge = assessment.net_edge
    return {
        "net_edge": None if net_edge is None else str(net_edge),
        "minimum_net_edge": str(policy.minimum_net_edge),
        "uncertainty_buffer": str(policy.uncertainty_buffer),
        "net_edge_minus_minimum": None
        if net_edge is None
        else str(net_edge - policy.minimum_net_edge),
        "counterfactual_net_edge": pricing.get("counterfactual_net_edge"),
        "counterfactual_edge_margin": pricing.get("counterfactual_edge_margin"),
    }


def _pricing_cost(pricing: DepthSimulation | None) -> str | None:
    if pricing is None or pricing.all_in_cost_per_share is None:
        return None
    return str(pricing.all_in_cost_per_share)


def _pricing_attr(pricing: DepthSimulation | None, attr: str) -> str | None:
    if pricing is None:
        return None
    value = getattr(pricing, attr)
    return None if value is None else str(value)
