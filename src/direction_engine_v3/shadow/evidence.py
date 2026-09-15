"""Immutable V3.15 shadow evidence contracts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import MarketBucket

_ZERO = Decimal("0")


class SettlementStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED_SETTLEMENT = "UNVERIFIED_SETTLEMENT"


class PromotionState(StrEnum):
    PROMOTE_CANDIDATE = "PROMOTE_CANDIDATE"
    CONTINUE_SHADOW = "CONTINUE_SHADOW"
    REJECT = "REJECT"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    INSUFFICIENT_TRADE_SAMPLE = "INSUFFICIENT_TRADE_SAMPLE"
    BLOCKED_DATA_QUALITY = "BLOCKED_DATA_QUALITY"
    BLOCKED_MODEL_QUALITY = "BLOCKED_MODEL_QUALITY"
    BLOCKED_EXECUTION_QUALITY = "BLOCKED_EXECUTION_QUALITY"
    MARKET_NOT_AVAILABLE = "MARKET_NOT_AVAILABLE"


@dataclass(frozen=True, slots=True)
class AWSIdentityEvidence:
    user_id: str
    account: str
    arn: str
    checked_at: datetime

    def __post_init__(self) -> None:
        require_text("user_id", self.user_id)
        require_text("account", self.account)
        require_text("arn", self.arn)
        require_utc("checked_at", self.checked_at)

    @property
    def is_root(self) -> bool:
        return self.arn.endswith(":root")

    @property
    def security_debt(self) -> str | None:
        return "AWS_ROOT_PROFILE_SECURITY_DEBT" if self.is_root else None

    def as_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "account": self.account,
            "arn": self.arn,
            "checked_at": self.checked_at.isoformat(),
            "is_root": self.is_root,
            "security_debt": self.security_debt,
        }


@dataclass(frozen=True, slots=True)
class EvidenceFingerprint:
    code_commit: str
    strategy_version: str
    model_version: str
    calibration_version: str
    feature_schema_version: str
    risk_policy_version: str
    router_policy_version: str
    execution_policy_version: str
    config_hash: str
    artifact_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        for name in (
            "code_commit",
            "strategy_version",
            "model_version",
            "calibration_version",
            "feature_schema_version",
            "risk_policy_version",
            "router_policy_version",
            "execution_policy_version",
            "config_hash",
        ):
            require_text(name, getattr(self, name))
        for key, value in self.artifact_hashes:
            require_text("artifact_hash key", key)
            require_text("artifact_hash value", value)

    @property
    def fingerprint_hash(self) -> str:
        payload = {
            "artifact_hashes": sorted(self.artifact_hashes),
            "calibration_version": self.calibration_version,
            "code_commit": self.code_commit,
            "config_hash": self.config_hash,
            "execution_policy_version": self.execution_policy_version,
            "feature_schema_version": self.feature_schema_version,
            "model_version": self.model_version,
            "risk_policy_version": self.risk_policy_version,
            "router_policy_version": self.router_policy_version,
            "strategy_version": self.strategy_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "code_commit": self.code_commit,
            "strategy_version": self.strategy_version,
            "model_version": self.model_version,
            "calibration_version": self.calibration_version,
            "feature_schema_version": self.feature_schema_version,
            "risk_policy_version": self.risk_policy_version,
            "router_policy_version": self.router_policy_version,
            "execution_policy_version": self.execution_policy_version,
            "config_hash": self.config_hash,
            "artifact_hashes": dict(self.artifact_hashes),
            "fingerprint_hash": self.fingerprint_hash,
        }


@dataclass(frozen=True, slots=True)
class EvidenceWindow:
    window_id: str
    started_at: datetime
    fingerprint: EvidenceFingerprint
    aws_identity: AWSIdentityEvidence

    def __post_init__(self) -> None:
        require_text("window_id", self.window_id)
        require_utc("started_at", self.started_at)

    def as_dict(self) -> dict[str, object]:
        return {
            "window_id": self.window_id,
            "started_at": self.started_at.isoformat(),
            "fingerprint": self.fingerprint.as_dict(),
            "aws_identity": self.aws_identity.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class BucketEvidence:
    bucket: MarketBucket
    forecast_count: int = 0
    verified_resolved_count: int = 0
    unresolved_count: int = 0
    paper_trade_count: int = 0
    abstain_count: int = 0
    brier_sum: Decimal = _ZERO
    log_loss_sum: Decimal = _ZERO
    net_pnl: Decimal = _ZERO
    maximum_drawdown: Decimal = _ZERO
    fail_closed_reasons: tuple[tuple[str, int], ...] = ()
    market_available: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.bucket, MarketBucket):
            raise TypeError("bucket must be MarketBucket")
        for name in (
            "forecast_count",
            "verified_resolved_count",
            "unresolved_count",
            "paper_trade_count",
            "abstain_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("brier_sum", "log_loss_sum", "net_pnl", "maximum_drawdown"):
            require_decimal(name, getattr(self, name))
        for reason, count in self.fail_closed_reasons:
            require_text("fail_closed_reason", reason)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("fail_closed reason counts must be non-negative integers")

    @property
    def promotion_state(self) -> PromotionState:
        if not self.market_available:
            return PromotionState.MARKET_NOT_AVAILABLE
        if self.verified_resolved_count < 100:
            return PromotionState.INSUFFICIENT_SAMPLE
        if self.paper_trade_count < 30:
            return PromotionState.INSUFFICIENT_TRADE_SAMPLE
        return PromotionState.CONTINUE_SHADOW

    def as_dict(self) -> dict[str, object]:
        resolved = Decimal(self.verified_resolved_count)
        return {
            "asset": self.bucket.asset.value,
            "horizon": self.bucket.horizon.value,
            "forecast_count": self.forecast_count,
            "verified_resolved_count": self.verified_resolved_count,
            "unresolved_count": self.unresolved_count,
            "paper_trade_count": self.paper_trade_count,
            "abstain_count": self.abstain_count,
            "brier": str(self.brier_sum / resolved) if resolved else None,
            "log_loss": str(self.log_loss_sum / resolved) if resolved else None,
            "net_pnl": str(self.net_pnl),
            "maximum_drawdown": str(self.maximum_drawdown),
            "fail_closed_reasons": dict(self.fail_closed_reasons),
            "promotion_state": self.promotion_state.value,
        }


@dataclass(frozen=True, slots=True)
class StructuralEvidence:
    raw_opportunities: int = 0
    fee_valid_opportunities: int = 0
    depth_valid_opportunities: int = 0
    executable_opportunities: int = 0
    both_leg_completion_rate: Decimal = _ZERO
    one_leg_rate: Decimal = _ZERO
    unwind_loss: Decimal = _ZERO
    residual_exposure_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "raw_opportunities",
            "fee_valid_opportunities",
            "depth_valid_opportunities",
            "executable_opportunities",
            "residual_exposure_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("both_leg_completion_rate", "one_leg_rate", "unwind_loss"):
            require_decimal(name, getattr(self, name), minimum=_ZERO)

    @property
    def promotion_state(self) -> PromotionState:
        if self.executable_opportunities < 50:
            return PromotionState.INSUFFICIENT_SAMPLE
        if self.residual_exposure_count:
            return PromotionState.BLOCKED_EXECUTION_QUALITY
        return PromotionState.CONTINUE_SHADOW

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_opportunities": self.raw_opportunities,
            "fee_valid_opportunities": self.fee_valid_opportunities,
            "depth_valid_opportunities": self.depth_valid_opportunities,
            "executable_opportunities": self.executable_opportunities,
            "both_leg_completion_rate": str(self.both_leg_completion_rate),
            "one_leg_rate": str(self.one_leg_rate),
            "unwind_loss": str(self.unwind_loss),
            "residual_exposure_count": self.residual_exposure_count,
            "promotion_state": self.promotion_state.value,
        }


@dataclass(frozen=True, slots=True)
class ShadowSummary:
    generated_at: datetime
    evidence_window: EvidenceWindow
    buckets: tuple[BucketEvidence, ...]
    structural: StructuralEvidence
    live_trading_enabled: bool
    live_auto_arm: bool
    real_order_submission: bool
    status: str

    def __post_init__(self) -> None:
        require_utc("generated_at", self.generated_at)
        if len(self.buckets) != 12:
            raise ValueError("shadow summary requires exactly twelve buckets")
        seen = {(item.bucket.asset, item.bucket.horizon) for item in self.buckets}
        expected = {(asset, horizon) for asset in Asset for horizon in Horizon}
        if seen != expected:
            raise ValueError("shadow summary scope must be BTC/ETH/SOL/XRP x 5m/15m/1h")
        if self.live_trading_enabled or self.live_auto_arm or self.real_order_submission:
            raise ValueError("V3.15 summary cannot report enabled LIVE or real orders")
        require_text("status", self.status)

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "status": self.status,
            "evidence_window": self.evidence_window.as_dict(),
            "buckets": [bucket.as_dict() for bucket in self.buckets],
            "structural": self.structural.as_dict(),
            "safety": {
                "app_mode": "PAPER",
                "live_trading_enabled": self.live_trading_enabled,
                "live_auto_arm": self.live_auto_arm,
                "real_order_submission": self.real_order_submission,
            },
        }
