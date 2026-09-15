"""Cluster-safe chronological walk-forward partitioning and promotion."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from direction_engine_v3.domain._validation import require_decimal
from direction_engine_v3.evaluation.metrics import EvaluationMetrics, EvaluationObservation

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train: tuple[EvaluationObservation, ...]
    test: tuple[EvaluationObservation, ...]


def walk_forward_folds(
    observations: tuple[EvaluationObservation, ...],
    *,
    minimum_train_clusters: int,
    test_clusters: int,
    embargo: timedelta,
) -> tuple[WalkForwardFold, ...]:
    if minimum_train_clusters < 1 or test_clusters < 1:
        raise ValueError("cluster counts must be positive")
    if embargo < timedelta(0):
        raise ValueError("embargo must be non-negative")
    if len({item.bucket for item in observations}) > 1:
        raise ValueError("walk-forward evaluation cannot pool asset/horizon buckets")
    grouped: dict[str, list[EvaluationObservation]] = {}
    for item in observations:
        grouped.setdefault(item.cluster_id, []).append(item)
    clusters = sorted(grouped.values(), key=lambda items: min(item.observed_at for item in items))
    folds = []
    for start in range(minimum_train_clusters, len(clusters), test_clusters):
        test_groups = clusters[start : start + test_clusters]
        if not test_groups:
            break
        test_start = min(item.observed_at for group in test_groups for item in group)
        train_groups = [
            group
            for group in clusters[:start]
            if max(item.observed_at for item in group) < test_start - embargo
        ]
        if len(train_groups) < minimum_train_clusters:
            continue
        train = tuple(
            sorted(
                (item for group in train_groups for item in group),
                key=lambda item: item.observed_at,
            )
        )
        test = tuple(
            sorted(
                (item for group in test_groups for item in group),
                key=lambda item: item.observed_at,
            )
        )
        folds.append(WalkForwardFold(train, test))
    return tuple(folds)


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_count: int
    maximum_ece: Decimal
    minimum_coverage: Decimal
    maximum_drawdown: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.minimum_count, bool) or not isinstance(self.minimum_count, int):
            raise TypeError("minimum_count must be an integer")
        if self.minimum_count < 1:
            raise ValueError("minimum_count must be positive")
        require_decimal("maximum_ece", self.maximum_ece, minimum=_ZERO, maximum=_ONE)
        require_decimal("minimum_coverage", self.minimum_coverage, minimum=_ZERO, maximum=_ONE)
        require_decimal("maximum_drawdown", self.maximum_drawdown, minimum=_ZERO)


def promotion_reasons(
    candidate: EvaluationMetrics,
    champion: EvaluationMetrics,
    policy: PromotionPolicy,
) -> tuple[str, ...]:
    reasons = []
    if candidate.count < policy.minimum_count:
        reasons.append("INSUFFICIENT_EVALUATION_SAMPLES")
    if candidate.brier >= champion.brier:
        reasons.append("BRIER_NOT_IMPROVED")
    if candidate.log_loss >= champion.log_loss:
        reasons.append("LOG_LOSS_NOT_IMPROVED")
    if candidate.ece > policy.maximum_ece:
        reasons.append("CALIBRATION_ERROR_TOO_HIGH")
    if candidate.coverage < policy.minimum_coverage:
        reasons.append("COVERAGE_TOO_LOW")
    if candidate.expectancy_after_costs is None or candidate.expectancy_after_costs <= _ZERO:
        reasons.append("EXPECTANCY_NOT_POSITIVE")
    if candidate.maximum_drawdown > policy.maximum_drawdown:
        reasons.append("DRAWDOWN_TOO_HIGH")
    return tuple(reasons)
