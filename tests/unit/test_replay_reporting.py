from decimal import Decimal

import pytest

from direction_engine_v3.evaluation import EvaluationMetrics
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.replay import (
    BucketStrategyReport,
    ReplayManifest,
    TwelveBucketReplayReport,
    canonical_hash,
)


def manifest() -> ReplayManifest:
    config = (("data_latency_ms", "25"), ("execution_latency_ms", "50"))
    return ReplayManifest(
        "dataset-v1", "abc123", "strategy-v1", "model-v1", config, 7, canonical_hash(config)
    )


def metrics() -> EvaluationMetrics:
    return EvaluationMetrics(
        100,
        Decimal(".2"),
        Decimal(".6"),
        Decimal(".04"),
        Decimal(".6"),
        Decimal(".2"),
        Decimal(".01"),
        Decimal("5"),
    )


def test_manifest_hash_is_order_independent_and_detects_change() -> None:
    assert canonical_hash((("b", "2"), ("a", "1"))) == canonical_hash((("a", "1"), ("b", "2")))
    with pytest.raises(ValueError, match="hash"):
        ReplayManifest("d", "c", "s", "m", (("x", "1"),), 0, "wrong")


def test_report_requires_all_buckets_and_explicit_non_pnl_promotion_result() -> None:
    reports = tuple(
        BucketStrategyReport(bucket, metrics(), 10, 5, ("NO_REAL_DATA",))
        for bucket in SUPPORTED_MARKET_BUCKETS
    )
    result = TwelveBucketReplayReport(manifest(), reports)
    assert len(result.buckets) == 12
    with pytest.raises(ValueError, match="twelve"):
        TwelveBucketReplayReport(manifest(), reports[:-1])
    with pytest.raises(ValueError, match="evidence"):
        TwelveBucketReplayReport(
            manifest(),
            tuple(
                BucketStrategyReport(bucket, metrics(), 10, 5, ())
                for bucket in SUPPORTED_MARKET_BUCKETS
            ),
        )
