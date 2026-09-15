"""Frozen reliability calibration learned from past-only observations."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise

from direction_engine_v3.domain import Asset, FeatureVector, Horizon, ProbabilityForecast
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.models.logistic import LogisticArtifact

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    raw_p_up: Decimal
    outcome_up: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        require_decimal("raw_p_up", self.raw_p_up, minimum=_ZERO, maximum=_ONE)
        if not isinstance(self.outcome_up, bool):
            raise TypeError("outcome_up must be a bool")
        require_utc("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: Decimal
    upper: Decimal
    calibrated_p_up: Decimal
    sample_count: int

    def __post_init__(self) -> None:
        require_decimal("lower", self.lower, minimum=_ZERO, maximum=_ONE)
        require_decimal("upper", self.upper, minimum=_ZERO, maximum=_ONE)
        require_decimal(
            "calibrated_p_up", self.calibrated_p_up, minimum=_ZERO, maximum=_ONE
        )
        if self.lower >= self.upper:
            raise ValueError("calibration bin lower must precede upper")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int):
            raise TypeError("sample_count must be an integer")
        if self.sample_count < 1:
            raise ValueError("sample_count must be positive")


@dataclass(frozen=True, slots=True)
class ReliabilityCalibrator:
    asset: Asset
    horizon: Horizon
    calibration_version: str
    bins: tuple[CalibrationBin, ...]
    trained_through: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        require_text("calibration_version", self.calibration_version)
        require_utc("trained_through", self.trained_through)
        if not self.bins:
            raise ValueError("calibrator requires bins")
        if self.bins[0].lower != _ZERO or self.bins[-1].upper != _ONE:
            raise ValueError("calibration bins must cover zero through one")
        for previous, current in pairwise(self.bins):
            if previous.upper != current.lower:
                raise ValueError("calibration bins must be contiguous")

    @property
    def sample_count(self) -> int:
        return sum(item.sample_count for item in self.bins)

    def calibrate(self, raw_p_up: Decimal) -> Decimal:
        require_decimal("raw_p_up", raw_p_up, minimum=_ZERO, maximum=_ONE)
        for index, item in enumerate(self.bins):
            if item.lower <= raw_p_up < item.upper or (
                index == len(self.bins) - 1 and raw_p_up == _ONE
            ):
                return item.calibrated_p_up
        raise ValueError("raw probability is outside calibration bins")


def fit_reliability_calibrator(
    samples: tuple[CalibrationSample, ...],
    *,
    asset: Asset,
    horizon: Horizon,
    calibration_version: str,
    boundaries: tuple[Decimal, ...],
    minimum_bin_count: int,
    trained_through: datetime,
) -> ReliabilityCalibrator:
    """Fit fixed reliability buckets using only samples available by the cutoff."""

    require_utc("trained_through", trained_through)
    if minimum_bin_count < 1:
        raise ValueError("minimum_bin_count must be positive")
    if len(boundaries) < 2 or boundaries[0] != _ZERO or boundaries[-1] != _ONE:
        raise ValueError("boundaries must span zero through one")
    if any(left >= right for left, right in pairwise(boundaries)):
        raise ValueError("boundaries must be strictly increasing")
    if any(sample.observed_at > trained_through for sample in samples):
        raise ValueError("calibration sample occurs after trained_through")
    bins = []
    for index, (lower, upper) in enumerate(pairwise(boundaries)):
        members = [
            sample
            for sample in samples
            if lower <= sample.raw_p_up < upper
            or (index == len(boundaries) - 2 and sample.raw_p_up == _ONE)
        ]
        if len(members) < minimum_bin_count:
            raise ValueError("calibration bin has insufficient samples")
        observed_rate = Decimal(sum(int(sample.outcome_up) for sample in members)) / Decimal(
            len(members)
        )
        bins.append(CalibrationBin(lower, upper, observed_rate, len(members)))
    return ReliabilityCalibrator(
        asset=asset,
        horizon=horizon,
        calibration_version=calibration_version,
        bins=tuple(bins),
        trained_through=trained_through,
    )


def calibrated_forecast(
    artifact: LogisticArtifact,
    calibrator: ReliabilityCalibrator,
    features: FeatureVector,
    *,
    generated_at: datetime,
) -> ProbabilityForecast:
    if features.asset is not calibrator.asset or features.horizon is not calibrator.horizon:
        raise ValueError("calibrator bucket mismatch")
    if features.generated_at <= calibrator.trained_through:
        raise ValueError("forecast features must follow calibration training data")
    raw = artifact.predict_raw(features)
    calibrated = calibrator.calibrate(raw)
    return ProbabilityForecast(
        market_id=features.market_id,
        asset=features.asset,
        horizon=features.horizon,
        p_up=calibrated,
        p_down=_ONE - calibrated,
        model_version=artifact.model_version,
        calibration_version=calibrator.calibration_version,
        feature_set_version=features.feature_set_version,
        feature_as_of=features.generated_at,
        generated_at=generated_at,
    )
