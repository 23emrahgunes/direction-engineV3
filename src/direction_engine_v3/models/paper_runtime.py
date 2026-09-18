"""PAPER-only Directional model loading and research baseline forecasts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain import Asset, FeatureVector, Horizon, ProbabilityForecast
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.models.calibration import (
    CalibrationBin,
    ReliabilityCalibrator,
)
from direction_engine_v3.models.contracts import CalibrationReadiness
from direction_engine_v3.models.logistic import LogisticArtifact, feature_schema_hash
from direction_engine_v3.models.registry import BucketModelState, TwelveBucketRegistry
from direction_engine_v3.storage import DirectionalTrainingRecord, SQLiteDirectionalCorpusRepository

PAPER_RESEARCH_BASELINE_MODEL_VERSION = "PAPER_RESEARCH_BASELINE"
PAPER_RESEARCH_BASELINE_CALIBRATION_VERSION = "PAPER_RESEARCH_BASELINE_UNPROMOTABLE"
PAPER_TRAINED_MODEL_VERSION_PREFIX = "PAPER_LOGISTIC"
PAPER_TRAINED_CALIBRATION_VERSION_PREFIX = "PAPER_RELIABILITY"

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_MIN_PROBABILITY = Decimal("0.01")
_MAX_PROBABILITY = Decimal("0.99")


@dataclass(frozen=True, slots=True)
class PaperBucketTrainingReport:
    asset: Asset
    horizon: Horizon
    current_count: int
    required_count: int
    state: str
    reason: str
    model_version: str | None = None
    calibration_version: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "asset": self.asset.value,
            "horizon": self.horizon.value,
            "current_count": self.current_count,
            "required_count": self.required_count,
            "state": self.state,
            "reason": self.reason,
            "model_version": self.model_version,
            "calibration_version": self.calibration_version,
            "live_ready": False,
        }


@dataclass(frozen=True, slots=True)
class PaperRegistryLoadResult:
    registry: TwelveBucketRegistry
    reports: tuple[PaperBucketTrainingReport, ...]

    def report_for(self, bucket: MarketBucket) -> PaperBucketTrainingReport:
        return next(
            item
            for item in self.reports
            if item.asset is bucket.asset and item.horizon is bucket.horizon
        )


def load_paper_registry_from_corpus(
    repository: SQLiteDirectionalCorpusRepository | None,
    *,
    minimum_samples: int = 100,
) -> PaperRegistryLoadResult:
    """Build exact-bucket PAPER models from labeled official-outcome corpus records."""

    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    states: list[BucketModelState] = []
    reports: list[PaperBucketTrainingReport] = []
    for bucket in SUPPORTED_MARKET_BUCKETS:
        records = (
            ()
            if repository is None
            else repository.training_ready_records(asset=bucket.asset, horizon=bucket.horizon)
        )
        count = len({item.condition_id for item in records})
        if count < minimum_samples:
            readiness = CalibrationReadiness(
                bucket.asset,
                bucket.horizon,
                "UNPROMOTED",
                count,
                False,
            )
            states.append(BucketModelState(bucket, readiness))
            reports.append(
                PaperBucketTrainingReport(
                    bucket.asset,
                    bucket.horizon,
                    count,
                    minimum_samples,
                    "INSUFFICIENT_SAMPLE" if count else "TRAINING_CORPUS_REQUIRED",
                    "INSUFFICIENT_OFFICIAL_LABELED_CORPUS"
                    if count
                    else "TRAINING_CORPUS_REQUIRED",
                )
            )
            continue
        artifact, calibrator = _fit_intercept_only_bucket(bucket, records)
        readiness = CalibrationReadiness(
            bucket.asset,
            bucket.horizon,
            calibrator.calibration_version,
            calibrator.sample_count,
            True,
        )
        states.append(BucketModelState(bucket, readiness, artifact, calibrator))
        reports.append(
            PaperBucketTrainingReport(
                bucket.asset,
                bucket.horizon,
                count,
                minimum_samples,
                "SHADOW_CANDIDATE",
                "PAPER_BUCKET_MODEL_READY",
                artifact.model_version,
                calibrator.calibration_version,
            )
        )
    return PaperRegistryLoadResult(TwelveBucketRegistry(tuple(states)), tuple(reports))


def paper_research_baseline_forecast(
    features: FeatureVector,
    *,
    generated_at: datetime,
) -> tuple[ProbabilityForecast, CalibrationReadiness]:
    """Produce a deterministic PAPER-only forecast from external/PTB features."""

    feature_map = {feature.name: feature.value for feature in features.features}
    for forbidden in ("polymarket", "clob", "contract_price"):
        if any(forbidden in name.lower() for name in feature_map):
            raise ValueError("PAPER baseline cannot consume Polymarket price alpha")
    score = (
        feature_map.get("ptb_normalized_distance", _ZERO) * Decimal("6")
        + feature_map.get("momentum", _ZERO) * Decimal("2")
        + feature_map.get("short_return", _ZERO) * Decimal("2")
        + feature_map.get("trade_imbalance", _ZERO) * Decimal("0.10")
        + feature_map.get("external_book_imbalance", _ZERO) * Decimal("0.10")
        + feature_map.get("microprice_distance", _ZERO) * Decimal("2")
        + feature_map.get("regime_score", _ZERO) * Decimal("0.05")
    )
    p_up = _clamp(_HALF + score, _MIN_PROBABILITY, _MAX_PROBABILITY)
    forecast = ProbabilityForecast(
        market_id=features.market_id,
        asset=features.asset,
        horizon=features.horizon,
        p_up=p_up,
        p_down=_ONE - p_up,
        model_version=PAPER_RESEARCH_BASELINE_MODEL_VERSION,
        calibration_version=PAPER_RESEARCH_BASELINE_CALIBRATION_VERSION,
        feature_set_version=features.feature_set_version,
        feature_as_of=features.generated_at,
        generated_at=generated_at,
    )
    readiness = CalibrationReadiness(
        features.asset,
        features.horizon,
        PAPER_RESEARCH_BASELINE_CALIBRATION_VERSION,
        0,
        True,
    )
    return forecast, readiness


def _fit_intercept_only_bucket(
    bucket: MarketBucket,
    records: tuple[DirectionalTrainingRecord, ...],
) -> tuple[LogisticArtifact, ReliabilityCalibrator]:
    first = records[0]
    payload = first.payload
    feature_vector = payload["feature_vector"]
    if not isinstance(feature_vector, dict):
        raise ValueError("training payload feature_vector must be an object")
    features = feature_vector["features"]
    if not isinstance(features, list):
        raise ValueError("training payload features must be a list")
    feature_names = tuple(
        str(item["name"]) for item in features if isinstance(item, dict)
    )
    observed_up = sum(1 for item in records if item.outcome_up)
    base_rate = Decimal(observed_up) / Decimal(len(records))
    base_rate = _clamp(base_rate, _MIN_PROBABILITY, _MAX_PROBABILITY)
    odds = base_rate / (_ONE - base_rate)
    artifact = LogisticArtifact(
        model_version=f"{PAPER_TRAINED_MODEL_VERSION_PREFIX}:{bucket.asset.value}:{bucket.horizon.value}",
        feature_set_version=str(feature_vector["feature_set_version"]),
        feature_names=feature_names,
        coefficients=tuple(_ZERO for _ in feature_names),
        intercept=odds.ln(),
        schema_hash=feature_schema_hash(feature_names),
    )
    latest_observed_at = max(item.observed_at for item in records)
    calibrator = ReliabilityCalibrator(
        asset=bucket.asset,
        horizon=bucket.horizon,
        calibration_version=(
            f"{PAPER_TRAINED_CALIBRATION_VERSION_PREFIX}:"
            f"{bucket.asset.value}:{bucket.horizon.value}"
        ),
        bins=(CalibrationBin(_ZERO, _ONE, base_rate, len(records)),),
        trained_through=latest_observed_at,
    )
    return artifact, calibrator


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))
