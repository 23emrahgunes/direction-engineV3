from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, FeatureValue, FeatureVector, Horizon
from direction_engine_v3.evaluation import (
    EvaluationMetrics,
    EvaluationObservation,
    PromotionPolicy,
    evaluate,
    promotion_reasons,
    walk_forward_folds,
)
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.models import (
    BucketModelState,
    CalibrationReadiness,
    CalibrationSample,
    LogisticArtifact,
    ModelNotReadyError,
    TwelveBucketRegistry,
    empty_registry,
    feature_schema_hash,
    fit_reliability_calibrator,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
NAMES = ("ptb_normalized_distance", "tte_fraction")


def artifact() -> LogisticArtifact:
    return LogisticArtifact(
        model_version="logistic-v1",
        feature_set_version="external-v1",
        feature_names=NAMES,
        coefficients=(Decimal("10"), Decimal("-0.1")),
        intercept=Decimal("0"),
        schema_hash=feature_schema_hash(NAMES),
    )


def vector(*, generated_at: datetime = NOW) -> FeatureVector:
    return FeatureVector(
        market_id="market-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        feature_set_version="external-v1",
        features=(
            FeatureValue(NAMES[0], Decimal("0.02"), "external", generated_at),
            FeatureValue(NAMES[1], Decimal("0.5"), "external", generated_at),
        ),
        generated_at=generated_at,
    )


def calibrator():
    samples = (
        CalibrationSample(Decimal("0.25"), False, NOW - timedelta(days=2)),
        CalibrationSample(Decimal("0.30"), True, NOW - timedelta(days=2)),
        CalibrationSample(Decimal("0.70"), True, NOW - timedelta(days=1)),
        CalibrationSample(Decimal("0.75"), True, NOW - timedelta(days=1)),
    )
    return fit_reliability_calibrator(
        samples,
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        calibration_version="cal-v1",
        boundaries=(Decimal("0"), Decimal("0.5"), Decimal("1")),
        minimum_bin_count=2,
        trained_through=NOW - timedelta(hours=1),
    )


def test_logistic_artifact_is_deterministic_and_schema_frozen() -> None:
    probability = artifact().predict_raw(vector())
    assert Decimal("0") < probability < Decimal("1")
    assert artifact().predict_raw(vector()) == probability
    with pytest.raises(ValueError, match="exactly match"):
        artifact().predict_raw(
            replace(vector(), features=vector().features[:1])
        )
    with pytest.raises(ValueError, match="Polymarket"):
        feature_schema_hash(("polymarket_price",))


def test_calibration_is_past_only_fixed_bucket_and_versioned() -> None:
    fitted = calibrator()
    assert fitted.sample_count == 4
    assert fitted.calibrate(Decimal("0.2")) == Decimal("0.5")
    assert fitted.calibrate(Decimal("0.8")) == Decimal("1")
    future = CalibrationSample(Decimal("0.8"), True, NOW)
    with pytest.raises(ValueError, match="after"):
        fit_reliability_calibrator(
            (future,),
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            calibration_version="bad",
            boundaries=(Decimal("0"), Decimal("1")),
            minimum_bin_count=1,
            trained_through=NOW - timedelta(seconds=1),
        )


def test_twelve_bucket_registry_has_no_cross_bucket_fallback() -> None:
    registry = empty_registry()
    assert len(registry.states) == 12
    assert {state.bucket for state in registry.states} == set(SUPPORTED_MARKET_BUCKETS)
    with pytest.raises(ModelNotReadyError, match="BTC-5m"):
        registry.forecast(vector(), generated_at=NOW + timedelta(milliseconds=1))


def test_ready_bucket_produces_calibrated_versioned_forecast() -> None:
    fitted = calibrator()
    states = list(empty_registry().states)
    index = next(
        i
        for i, state in enumerate(states)
        if state.bucket == MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES)
    )
    states[index] = BucketModelState(
        states[index].bucket,
        CalibrationReadiness(Asset.BTC, Horizon.FIVE_MINUTES, "cal-v1", 4, True),
        artifact(),
        fitted,
    )
    registry = TwelveBucketRegistry(tuple(states))
    result = registry.forecast(vector(), generated_at=NOW + timedelta(milliseconds=1))
    assert result.calibration_version == "cal-v1"
    assert result.model_version == "logistic-v1"
    assert result.p_up + result.p_down == Decimal("1")


def observation(
    index: int,
    *,
    p_up: str,
    outcome: bool,
    executed: bool = True,
    pnl: str = "1",
    cluster: str | None = None,
) -> EvaluationObservation:
    return EvaluationObservation(
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        f"market-{index}",
        cluster or f"cluster-{index}",
        NOW + timedelta(minutes=index),
        Decimal(p_up),
        outcome,
        executed,
        Decimal(pnl) if executed else None,
    )


def test_metrics_cover_probability_calibration_trade_rate_and_drawdown() -> None:
    metrics = evaluate(
        (
            observation(0, p_up="0.8", outcome=True, pnl="2"),
            observation(1, p_up="0.2", outcome=False, pnl="-3"),
            observation(2, p_up="0.6", outcome=True, executed=False),
            observation(3, p_up="0.4", outcome=False, pnl="2"),
        )
    )
    assert metrics.brier == Decimal("0.10")
    assert metrics.accuracy == Decimal("1")
    assert metrics.coverage == Decimal("0.75")
    assert metrics.expectancy_after_costs == Decimal("0.3333333333333333333333333333")
    assert metrics.maximum_drawdown == Decimal("3")
    assert metrics.ece >= 0
    assert metrics.log_loss > 0


def test_walk_forward_keeps_clusters_whole_chronological_and_embargoed() -> None:
    observations = (
        observation(0, p_up="0.5", outcome=True, cluster="a"),
        observation(1, p_up="0.5", outcome=False, cluster="a"),
        observation(5, p_up="0.5", outcome=True, cluster="b"),
        observation(10, p_up="0.5", outcome=False, cluster="c"),
        observation(15, p_up="0.5", outcome=True, cluster="d"),
    )
    folds = walk_forward_folds(
        observations,
        minimum_train_clusters=2,
        test_clusters=1,
        embargo=timedelta(minutes=3),
    )
    assert folds
    for fold in folds:
        train_clusters = {item.cluster_id for item in fold.train}
        test_clusters = {item.cluster_id for item in fold.test}
        assert train_clusters.isdisjoint(test_clusters)
        assert max(item.observed_at for item in fold.train) < min(
            item.observed_at for item in fold.test
        ) - timedelta(minutes=3)


def test_promotion_requires_joint_statistical_and_economic_quality() -> None:
    champion = EvaluationMetrics(
        100,
        Decimal("0.20"),
        Decimal("0.60"),
        Decimal("0.04"),
        Decimal("0.60"),
        Decimal("0.20"),
        Decimal("0.01"),
        Decimal("5"),
    )
    candidate = replace(
        champion,
        brier=Decimal("0.19"),
        log_loss=Decimal("0.58"),
        expectancy_after_costs=Decimal("0.02"),
    )
    policy = PromotionPolicy(100, Decimal("0.05"), Decimal("0.10"), Decimal("10"))
    assert promotion_reasons(candidate, champion, policy) == ()
    rejected = replace(candidate, expectancy_after_costs=Decimal("-0.01"))
    assert "EXPECTANCY_NOT_POSITIVE" in promotion_reasons(rejected, champion, policy)
    too_small = replace(candidate, count=99)
    assert "INSUFFICIENT_EVALUATION_SAMPLES" in promotion_reasons(
        too_small, champion, policy
    )


def test_evaluation_never_pools_the_twelve_buckets() -> None:
    other = replace(observation(1, p_up="0.5", outcome=True), asset=Asset.ETH)
    with pytest.raises(ValueError, match="pool"):
        evaluate((observation(0, p_up="0.5", outcome=True), other))
    with pytest.raises(ValueError, match="pool"):
        walk_forward_folds(
            (observation(0, p_up="0.5", outcome=True), other),
            minimum_train_clusters=1,
            test_clusters=1,
            embargo=timedelta(0),
        )
