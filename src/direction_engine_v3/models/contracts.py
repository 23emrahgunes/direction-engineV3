"""Immutable model-readiness contracts."""

from dataclasses import dataclass

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class CalibrationReadiness:
    asset: Asset
    horizon: Horizon
    calibration_version: str
    sample_count: int
    ready: bool

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")
        require_text("calibration_version", self.calibration_version)
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count < 0
        ):
            raise ValueError("sample_count must be a non-negative integer")
        if not isinstance(self.ready, bool):
            raise TypeError("ready must be a bool")
