"""Shadow-only model state labels that do not imply LIVE promotion."""

from dataclasses import dataclass
from enum import StrEnum

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.models.contracts import CalibrationReadiness


class ShadowModelState(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    TRAINING_CORPUS_REQUIRED = "TRAINING_CORPUS_REQUIRED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    SHADOW_CANDIDATE = "SHADOW_CANDIDATE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ShadowBucketModelStatus:
    asset: Asset
    horizon: Horizon
    state: ShadowModelState
    readiness: CalibrationReadiness
    reason: str
    model_version: str | None = None
    calibration_version: str | None = None
    corpus_sample_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        if not isinstance(self.state, ShadowModelState):
            raise TypeError("state must be ShadowModelState")
        if self.state is not ShadowModelState.SHADOW_CANDIDATE and self.readiness.ready:
            raise ValueError("only SHADOW_CANDIDATE may carry ready calibration")
        if self.corpus_sample_count < 0:
            raise ValueError("corpus_sample_count must be non-negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "asset": self.asset.value,
            "horizon": self.horizon.value,
            "state": self.state.value,
            "reason": self.reason,
            "ready": self.readiness.ready,
            "sample_count": self.readiness.sample_count,
            "model_version": self.model_version,
            "calibration_version": self.calibration_version or self.readiness.calibration_version,
            "corpus_sample_count": self.corpus_sample_count,
            "live_ready": False,
        }
