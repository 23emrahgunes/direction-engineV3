"""Strict twelve-bucket model registry with no cross-bucket fallback."""

from dataclasses import dataclass
from datetime import datetime

from direction_engine_v3.domain import FeatureVector, ProbabilityForecast
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.models.calibration import ReliabilityCalibrator, calibrated_forecast
from direction_engine_v3.models.contracts import CalibrationReadiness
from direction_engine_v3.models.logistic import LogisticArtifact


class ModelNotReadyError(RuntimeError):
    """The exact asset/horizon bucket has no promoted calibrated artifact."""


@dataclass(frozen=True, slots=True)
class BucketModelState:
    bucket: MarketBucket
    readiness: CalibrationReadiness
    artifact: LogisticArtifact | None = None
    calibrator: ReliabilityCalibrator | None = None

    def __post_init__(self) -> None:
        if self.readiness.asset is not self.bucket.asset:
            raise ValueError("readiness asset mismatch")
        if self.readiness.horizon is not self.bucket.horizon:
            raise ValueError("readiness horizon mismatch")
        has_pair = self.artifact is not None and self.calibrator is not None
        if self.readiness.ready != has_pair:
            raise ValueError("ready state requires both artifact and calibrator")
        if self.calibrator is not None:
            if self.calibrator.asset is not self.bucket.asset:
                raise ValueError("calibrator asset mismatch")
            if self.calibrator.horizon is not self.bucket.horizon:
                raise ValueError("calibrator horizon mismatch")
            if self.calibrator.calibration_version != self.readiness.calibration_version:
                raise ValueError("calibration version mismatch")
            if self.calibrator.sample_count != self.readiness.sample_count:
                raise ValueError("calibration sample count mismatch")


@dataclass(frozen=True, slots=True)
class TwelveBucketRegistry:
    states: tuple[BucketModelState, ...]

    def __post_init__(self) -> None:
        buckets = [state.bucket for state in self.states]
        if len(buckets) != 12 or set(buckets) != set(SUPPORTED_MARKET_BUCKETS):
            raise ValueError("registry must contain each supported bucket exactly once")
        if len(set(buckets)) != len(buckets):
            raise ValueError("registry buckets must be unique")

    def state_for(self, bucket: MarketBucket) -> BucketModelState:
        return next(state for state in self.states if state.bucket == bucket)

    def forecast(self, features: FeatureVector, *, generated_at: datetime) -> ProbabilityForecast:
        state = self.state_for(MarketBucket(features.asset, features.horizon))
        if not state.readiness.ready or state.artifact is None or state.calibrator is None:
            raise ModelNotReadyError(
                f"model bucket {features.asset.value}-{features.horizon.value} is not ready"
            )
        return calibrated_forecast(
            state.artifact, state.calibrator, features, generated_at=generated_at
        )


def empty_registry(*, calibration_version: str = "UNPROMOTED") -> TwelveBucketRegistry:
    return TwelveBucketRegistry(
        tuple(
            BucketModelState(
                bucket=bucket,
                readiness=CalibrationReadiness(
                    bucket.asset, bucket.horizon, calibration_version, 0, False
                ),
            )
            for bucket in SUPPORTED_MARKET_BUCKETS
        )
    )
