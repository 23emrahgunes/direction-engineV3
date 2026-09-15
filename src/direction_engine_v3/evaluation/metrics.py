"""Probability, calibration, coverage, and economic evaluation metrics."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import MarketBucket

_ZERO = Decimal("0")
_ONE = Decimal("1")
_EPSILON = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    asset: Asset
    horizon: Horizon
    market_id: str
    cluster_id: str
    observed_at: datetime
    p_up: Decimal
    outcome_up: bool
    executed: bool
    pnl_after_costs: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        require_text("market_id", self.market_id)
        require_text("cluster_id", self.cluster_id)
        require_utc("observed_at", self.observed_at)
        require_decimal("p_up", self.p_up, minimum=_ZERO, maximum=_ONE)
        if not isinstance(self.outcome_up, bool) or not isinstance(self.executed, bool):
            raise TypeError("outcome_up and executed must be bools")
        if self.pnl_after_costs is not None:
            require_decimal("pnl_after_costs", self.pnl_after_costs)
        if self.executed != (self.pnl_after_costs is not None):
            raise ValueError("executed observations require PnL and abstentions must omit it")

    @property
    def bucket(self) -> MarketBucket:
        return MarketBucket(self.asset, self.horizon)


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    count: int
    brier: Decimal
    log_loss: Decimal
    ece: Decimal
    accuracy: Decimal
    coverage: Decimal
    expectancy_after_costs: Decimal | None
    maximum_drawdown: Decimal


def evaluate(observations: tuple[EvaluationObservation, ...]) -> EvaluationMetrics:
    if not observations:
        raise ValueError("evaluation requires observations")
    buckets = {item.bucket for item in observations}
    if len(buckets) != 1:
        raise ValueError("evaluation cannot pool different asset/horizon buckets")
    count = Decimal(len(observations))
    outcomes = [Decimal(int(item.outcome_up)) for item in observations]
    brier = sum(
        ((item.p_up - outcome) ** 2 for item, outcome in zip(observations, outcomes, strict=True)),
        _ZERO,
    ) / count
    losses = []
    correct = 0
    for item, outcome in zip(observations, outcomes, strict=True):
        probability = min(_ONE - _EPSILON, max(_EPSILON, item.p_up))
        losses.append(-(outcome * probability.ln() + (_ONE - outcome) * (_ONE - probability).ln()))
        predicted_up = item.p_up >= Decimal("0.5")
        correct += int(predicted_up == item.outcome_up)
    executed = [item.pnl_after_costs for item in observations if item.pnl_after_costs is not None]
    expectancy = sum(executed, _ZERO) / Decimal(len(executed)) if executed else None
    return EvaluationMetrics(
        count=len(observations),
        brier=brier,
        log_loss=sum(losses, _ZERO) / count,
        ece=_ece(observations),
        accuracy=Decimal(correct) / count,
        coverage=Decimal(len(executed)) / count,
        expectancy_after_costs=expectancy,
        maximum_drawdown=_maximum_drawdown(executed),
    )


def _ece(observations: tuple[EvaluationObservation, ...]) -> Decimal:
    total = Decimal(len(observations))
    result = _ZERO
    for index in range(10):
        lower = Decimal(index) / Decimal(10)
        upper = Decimal(index + 1) / Decimal(10)
        members = [
            item
            for item in observations
            if lower <= item.p_up < upper or (index == 9 and item.p_up == _ONE)
        ]
        if members:
            confidence = sum((item.p_up for item in members), _ZERO) / Decimal(len(members))
            observed = Decimal(sum(int(item.outcome_up) for item in members)) / Decimal(
                len(members)
            )
            result += Decimal(len(members)) / total * abs(confidence - observed)
    return result


def _maximum_drawdown(pnls: list[Decimal]) -> Decimal:
    equity = peak = _ZERO
    maximum = _ZERO
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum
