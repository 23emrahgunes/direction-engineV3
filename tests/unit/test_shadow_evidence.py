from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.shadow import (
    AWSIdentityEvidence,
    BucketEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
    PromotionState,
    ShadowSummary,
    StructuralEvidence,
    build_shadow_summary,
)


def _fingerprint() -> EvidenceFingerprint:
    return EvidenceFingerprint(
        code_commit="abc123",
        strategy_version="strategy",
        model_version="model",
        calibration_version="calibration",
        feature_schema_version="features",
        risk_policy_version="risk",
        router_policy_version="router",
        execution_policy_version="execution",
        config_hash="config",
        artifact_hashes=(("artifact", "hash"),),
    )


def _window() -> EvidenceWindow:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return EvidenceWindow(
        "window-1",
        now,
        _fingerprint(),
        AWSIdentityEvidence("605618941421", "605618941421", "arn:aws:iam::605618941421:root", now),
    )


def test_aws_root_identity_records_security_debt() -> None:
    identity = _window().aws_identity

    assert identity.is_root is True
    assert identity.security_debt == "AWS_ROOT_PROFILE_SECURITY_DEBT"


def test_fingerprint_hash_changes_after_material_config_change() -> None:
    original = _fingerprint()
    changed = EvidenceFingerprint(
        code_commit=original.code_commit,
        strategy_version=original.strategy_version,
        model_version=original.model_version,
        calibration_version=original.calibration_version,
        feature_schema_version=original.feature_schema_version,
        risk_policy_version=original.risk_policy_version,
        router_policy_version=original.router_policy_version,
        execution_policy_version=original.execution_policy_version,
        config_hash="changed",
        artifact_hashes=original.artifact_hashes,
    )

    assert original.fingerprint_hash != changed.fingerprint_hash


def test_bucket_evidence_reports_zero_trade_bucket_and_sample_gate() -> None:
    bucket = BucketEvidence(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))

    payload = bucket.as_dict()

    assert payload["paper_trade_count"] == 0
    assert payload["promotion_state"] == PromotionState.INSUFFICIENT_SAMPLE.value


def test_bucket_requires_official_verified_settlement_for_metrics() -> None:
    bucket = BucketEvidence(
        MarketBucket(Asset.ETH, Horizon.FIFTEEN_MINUTES),
        forecast_count=2,
        verified_resolved_count=1,
        unresolved_count=1,
        brier_sum=Decimal("0.25"),
        log_loss_sum=Decimal("0.70"),
    )

    payload = bucket.as_dict()

    assert payload["brier"] == "0.25"
    assert payload["unresolved_count"] == 1


def test_structural_evidence_sample_gate_and_residual_block() -> None:
    insufficient = StructuralEvidence(executable_opportunities=49)
    blocked = StructuralEvidence(executable_opportunities=50, residual_exposure_count=1)

    assert insufficient.promotion_state is PromotionState.INSUFFICIENT_SAMPLE
    assert blocked.promotion_state is PromotionState.BLOCKED_EXECUTION_QUALITY


def test_shadow_summary_requires_all_twelve_buckets_and_live_disabled() -> None:
    summary = build_shadow_summary(evidence_window=_window(), generated_at=_window().started_at)

    assert len(summary.buckets) == 12
    assert {item.bucket for item in summary.buckets} == set(SUPPORTED_MARKET_BUCKETS)
    assert summary.live_trading_enabled is False
    assert summary.live_auto_arm is False
    assert summary.real_order_submission is False

    with pytest.raises(ValueError, match="enabled LIVE"):
        ShadowSummary(
            generated_at=_window().started_at,
            evidence_window=_window(),
            buckets=summary.buckets,
            structural=StructuralEvidence(),
            live_trading_enabled=True,
            live_auto_arm=False,
            real_order_submission=False,
            status="bad",
        )
