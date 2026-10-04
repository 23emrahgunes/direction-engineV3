"""Security boundaries for probability artifacts and promotion readiness."""

import inspect
from datetime import UTC, datetime
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon, ProbabilityForecast
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.models import (
    PAPER_RESEARCH_BASELINE_MODEL_VERSION,
    ModelExecutionPermission,
    ModelGovernanceDecision,
    ModelGovernanceState,
    calibration,
    contracts,
    directional_paper_governance_decision,
    empty_registry,
    governance,
    logistic,
    registry,
)


def test_unpromoted_registry_fails_closed_for_all_twelve_buckets() -> None:
    registry = empty_registry()

    assert tuple(state.bucket for state in registry.states) == SUPPORTED_MARKET_BUCKETS
    assert all(not state.readiness.ready for state in registry.states)
    assert all(state.artifact is None for state in registry.states)
    assert all(state.calibrator is None for state in registry.states)


def test_model_package_has_no_network_order_or_unsafe_artifact_loading() -> None:
    source = "\n".join(
        inspect.getsource(module).lower()
        for module in (calibration, contracts, governance, logistic, registry)
    )

    for forbidden in (
        "aiohttp",
        "requests",
        "socket",
        "submit_order",
        "place_order",
        "pickle",
        "joblib",
    ):
        assert forbidden not in source


def test_p2_0_default_directional_governance_denies_paper_for_all_buckets() -> None:
    status = governance.default_directional_governance_status()

    assert status["real_order_submission"] is False
    assert len(status["buckets"]) == 12
    assert {
        (item["asset"], item["horizon"], item["execution_permission"])
        for item in status["buckets"]
    } == {
        (bucket.asset.value, bucket.horizon.value, ModelExecutionPermission.NONE.value)
        for bucket in SUPPORTED_MARKET_BUCKETS
    }
    assert all(
        item["governance_rejection_reason"] == "MODEL_NOT_PROMOTED"
        for item in status["buckets"]
    )


def test_paper_research_baseline_has_no_paper_execution_permission() -> None:
    forecast = ProbabilityForecast(
        "market",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.60"),
        Decimal("0.40"),
        PAPER_RESEARCH_BASELINE_MODEL_VERSION,
        "PAPER_RESEARCH_BASELINE_UNPROMOTABLE",
        "features",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    decision = directional_paper_governance_decision(
        MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES),
        forecast,
    )

    assert decision.paper_execution_allowed is False
    assert decision.execution_permission is ModelExecutionPermission.NONE
    assert decision.reason == "MODEL_NOT_PROMOTED"
    assert decision.as_dict()["real_order_submission"] is False


def test_limited_paper_permission_is_exact_bucket_and_model_scoped() -> None:
    promoted_bucket = MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES)
    promoted_model = "PAPER_LOGISTIC:BTC:5m:v2"
    promoted = {
        (promoted_bucket, promoted_model): ModelGovernanceDecision(
            promoted_bucket,
            ModelGovernanceState.LIMITED_PAPER,
            ModelExecutionPermission.LIMITED_PAPER,
            "PROMOTED_MODEL_AUTHORIZED",
            promoted_model,
            "promotion-1",
        )
    }
    forecast = ProbabilityForecast(
        "market",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.60"),
        Decimal("0.40"),
        promoted_model,
        "calibration-v2",
        "features",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    accepted = directional_paper_governance_decision(
        promoted_bucket,
        forecast,
        promoted,
    )
    rejected_other_bucket = directional_paper_governance_decision(
        MarketBucket(Asset.ETH, Horizon.FIVE_MINUTES),
        forecast,
        promoted,
    )

    assert accepted.paper_execution_allowed is True
    assert accepted.execution_permission is ModelExecutionPermission.LIMITED_PAPER
    assert accepted.promotion_id == "promotion-1"
    assert rejected_other_bucket.paper_execution_allowed is False
    assert rejected_other_bucket.reason == "MODEL_NOT_PROMOTED"
