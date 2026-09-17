"""Read-only dashboard snapshot assembly for V3.13."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from direction_engine_v3.config import (
    APP_MODE,
    LIVE_AUTO_ARM,
    LIVE_TRADING_ENABLED,
    SUPPORTED_ASSETS,
    SUPPORTED_HORIZONS,
)
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.observability import (
    ComponentHealth,
    HealthStatus,
    MarketMetric,
    MetricsSnapshot,
    ReadinessReport,
)
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLitePaperRepository


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    generated_at: datetime
    readiness: ReadinessReport
    metrics: MetricsSnapshot

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "mode": {
                "app_mode": APP_MODE,
                "live_trading_enabled": LIVE_TRADING_ENABLED,
                "live_auto_arm": LIVE_AUTO_ARM,
            },
            "scope": {
                "assets": list(SUPPORTED_ASSETS),
                "horizons": list(SUPPORTED_HORIZONS),
            },
            "readiness": self.readiness.as_dict(),
            "metrics": self.metrics.as_dict(),
            "strategy": {
                "directional_edge": "READ_ONLY_OBSERVABLE",
                "structural_arbitrage": "READ_ONLY_OBSERVABLE",
                "dual40": "DISABLED_BY_DEFAULT",
            },
            "execution": {
                "paper_gateway": "AVAILABLE",
                "live_gateway": "PRELIVE_GUARD_ONLY",
                "real_order_submission": False,
            },
        }


def build_dashboard_snapshot(*, now: datetime | None = None) -> DashboardSnapshot:
    generated_at = now or datetime.now(UTC)
    readiness = ReadinessReport(
        process_alive=ComponentHealth(
            "process",
            HealthStatus.PASSING,
            "dashboard process is alive",
            generated_at,
        ),
        database=ComponentHealth(
            "paper_ledger",
            HealthStatus.DEGRADED,
            "ledger path is checked by deployment smoke",
            generated_at,
        ),
        data_freshness=ComponentHealth(
            "market_data_freshness",
            HealthStatus.DEGRADED,
            "no live feed is required for dashboard import",
            generated_at,
        ),
        model_artifacts=ComponentHealth(
            "model_artifacts",
            HealthStatus.DEGRADED,
            "no promoted model artifact is required for PRE-LIVE dashboard",
            generated_at,
        ),
        trading_readiness=ComponentHealth(
            "trading_readiness",
            HealthStatus.FAILING,
            "LIVE is disabled and unarmed by default",
            generated_at,
        ),
    )
    metrics = MetricsSnapshot(
        generated_at=generated_at,
        markets=tuple(
            MarketMetric(
                asset=asset,
                horizon=horizon,
                book_coverage=False,
                official_reference_fresh=False,
                proxy_reference_fresh=False,
            )
            for asset in Asset
            for horizon in Horizon
        ),
    )
    return DashboardSnapshot(generated_at, readiness, metrics)


def runtime_data_dir() -> Path:
    """Return project runtime data directory; import-safe and credential-free."""

    import os

    return Path(os.environ.get("RUNTIME_DATA_DIR", "runtime/data"))


def build_paper_summary() -> dict[str, object]:
    repository = _paper_repository()
    return repository.summary()


def list_paper_trades(
    *,
    asset: str | None = None,
    horizon: str | None = None,
    strategy: str | None = None,
    side: str | None = None,
    status: str | None = None,
    win_loss: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> dict[str, object]:
    repository = _paper_repository()
    trades = repository.trades(
        asset=asset,
        horizon=horizon,
        strategy=strategy,
        side=side,
        status=status,
        win_loss=win_loss,
        limit=_safe_limit(limit),
        offset=_safe_offset(offset),
    )
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "trades": [_trade_as_dict(item) for item in trades],
    }


def get_paper_trade(trade_id: str) -> dict[str, object] | None:
    trade = _paper_repository().trade_by_id(trade_id)
    if trade is None:
        return None
    return _trade_as_dict(trade)


def list_paper_abstains(
    *,
    reason: str | None = None,
    strategy: str | None = None,
    asset: str | None = None,
    horizon: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> dict[str, object]:
    repository = _paper_repository()
    abstains = repository.abstains(
        reason=reason,
        strategy=strategy,
        asset=asset,
        horizon=horizon,
        limit=_safe_limit(limit),
        offset=_safe_offset(offset),
    )
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "abstains": [
            {
                "abstain_id": item.abstain_id,
                "strategy": item.strategy,
                "asset": item.asset,
                "horizon": item.horizon,
                "condition_id": item.condition_id,
                "reason": item.reason,
                "payload": dict(item.payload),
                "observed_at": item.observed_at.isoformat(),
            }
            for item in abstains
        ],
    }


def build_shadow_status() -> dict[str, object]:
    data_dir = runtime_data_dir()
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3")
    event_counts: dict[str, int]
    try:
        shadow.initialize()
        event_counts = shadow.event_counts()
    except Exception as exc:
        event_counts = {"SHADOW_STATUS_UNAVAILABLE": 1}
        return {
            "status": "SHADOW_STATUS_UNAVAILABLE",
            "reason": type(exc).__name__,
            "real_order_submission": False,
            "event_counts": event_counts,
            "paper_database": str(data_dir / "paper.sqlite3"),
        }
    return {
        "status": "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED"
        if event_counts.get("REAL_SHADOW_CYCLE", 0) > 0
        else "V3.15_REPORT_ONLY_WINDOW_INVALID_FOR_STRATEGY_BURN_IN",
        "previous_report_only_window_invalid_for_strategy_burn_in": True,
        "real_order_submission": False,
        "event_counts": event_counts,
        "paper_database": str(data_dir / "paper.sqlite3"),
    }


def build_directional_runtime_status() -> dict[str, object]:
    data_dir = runtime_data_dir()
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3")
    paper = _paper_repository()
    try:
        shadow.initialize()
        directional_events = shadow.latest_events(event_type="STRATEGY_EVALUATION", limit=500)
        pipeline_events = shadow.latest_events(event_type="MARKET_DATA_PIPELINE", limit=500)
    except Exception as exc:
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "status": "DIRECTIONAL_STATUS_UNAVAILABLE",
            "reason": type(exc).__name__,
            "real_order_submission": False,
            "buckets": [],
        }
    by_bucket: dict[str, dict[str, object]] = {}
    for event in directional_events:
        payload_obj = event.get("payload")
        if event["bucket_key"] is None or not isinstance(payload_obj, dict):
            continue
        payload = dict(payload_obj)
        bucket_key = str(event["bucket_key"])
        if payload.get("strategy") == "DIRECTIONAL_EDGE" and bucket_key not in by_bucket:
            by_bucket[bucket_key] = event
    pipeline_by_bucket: dict[str, dict[str, object]] = {}
    for event in pipeline_events:
        payload_obj = event.get("payload")
        if event["bucket_key"] is None or not isinstance(payload_obj, dict):
            continue
        bucket_key = str(event["bucket_key"])
        if bucket_key not in pipeline_by_bucket:
            pipeline_by_bucket[bucket_key] = event
    buckets = []
    for bucket in SUPPORTED_MARKET_BUCKETS:
        bucket_key = f"{bucket.asset.value}-{bucket.horizon.value}"
        latest = by_bucket.get(bucket_key)
        latest_pipeline = pipeline_by_bucket.get(bucket_key)
        latest_payload = latest["payload"] if latest is not None else {}
        payload = dict(latest_payload) if isinstance(latest_payload, dict) else {}
        pipeline_payload_obj = latest_pipeline["payload"] if latest_pipeline is not None else {}
        pipeline_payload = (
            dict(pipeline_payload_obj) if isinstance(pipeline_payload_obj, dict) else {}
        )
        strategy_observed_at = latest["observed_at"] if latest is not None else None
        pipeline_observed_at = (
            latest_pipeline["observed_at"] if latest_pipeline is not None else None
        )
        latest_observed_at = pipeline_payload.get("latest_observed_at", pipeline_observed_at)
        trades = paper.trades(
            asset=bucket.asset.value,
            horizon=bucket.horizon.value,
            strategy="DIRECTIONAL_EDGE",
            limit=1,
        )
        buckets.append(
            {
                "asset": bucket.asset.value,
                "horizon": bucket.horizon.value,
                "state": _directional_bucket_state(payload | pipeline_payload),
                "latest_observed_at": latest_observed_at,
                "pipeline_observed_at": pipeline_observed_at,
                "strategy_observed_at": strategy_observed_at,
                "discovery_status": pipeline_payload.get("discovery_status", "UNKNOWN"),
                "discovery_error": pipeline_payload.get("discovery_error"),
                "book_status": pipeline_payload.get("book_status", "UNKNOWN"),
                "book_error": pipeline_payload.get("book_error"),
                "fee_status": pipeline_payload.get("fee_status", "UNKNOWN"),
                "fee_error": pipeline_payload.get("fee_error"),
                "proxy_status": pipeline_payload.get(
                    "proxy_status", payload.get("proxy_status", "UNKNOWN")
                ),
                "proxy_error": pipeline_payload.get("proxy_error"),
                "official_status": pipeline_payload.get(
                    "official_status", payload.get("official_status", "UNKNOWN")
                ),
                "ptb_status": pipeline_payload.get(
                    "ptb_status", payload.get("ptb_status", "UNKNOWN")
                ),
                "ptb_reason": pipeline_payload.get(
                    "ptb_reason", payload.get("ptb_reason", "UNKNOWN")
                ),
                "ptb_value": pipeline_payload.get("ptb_value", payload.get("ptb_value")),
                "ptb_effective_time": pipeline_payload.get(
                    "ptb_effective_time", payload.get("ptb_effective_time")
                ),
                "feature_status": pipeline_payload.get(
                    "feature_status", payload.get("feature_status", "UNKNOWN")
                ),
                "feature_error": pipeline_payload.get("feature_error"),
                "model_state": payload.get("model_state", "TRAINING_CORPUS_REQUIRED"),
                "model_version": payload.get("model_version"),
                "training_report": payload.get("training_report", {}),
                "calibration_state": payload.get("calibration_state", "CALIBRATION_NOT_READY"),
                "calibration_version": payload.get("calibration_version"),
                "pricing_status": payload.get("pricing_status", "UNKNOWN"),
                "chainlink": pipeline_payload.get("chainlink", payload.get("chainlink", {})),
                "binance_hourly": pipeline_payload.get(
                    "binance_hourly", payload.get("binance_hourly", {})
                ),
                "pipeline_stages": pipeline_payload.get("pipeline_stages", ()),
                "last_decision": payload.get("action", "ABSTAIN"),
                "last_abstain_reason": payload.get("reason", "NO_RUNTIME_EVIDENCE"),
                "selected_side": payload.get("selected_side"),
                "executable_cost": payload.get("executable_cost"),
                "net_edge": payload.get("net_edge"),
                "directional_execution": payload.get("directional_execution", {}),
                "last_observed_at": latest_observed_at or strategy_observed_at,
                "last_paper_trade": _trade_as_dict(trades[0]) if trades else None,
                "evidence_sample_count": payload.get("corpus_sample_count", 0),
                "p_up": payload.get("p_up"),
                "p_down": payload.get("p_down"),
                "executable_up_cost": payload.get("executable_up_cost"),
                "executable_down_cost": payload.get("executable_down_cost"),
                "label": "PAPER / SHADOW — NO REAL ORDER",
            }
        )
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "status": "V3.15.2_DIRECTIONAL_RUNTIME_OBSERVABLE",
        "real_order_submission": False,
        "buckets": buckets,
    }


def _paper_repository() -> SQLitePaperRepository:
    repository = SQLitePaperRepository(runtime_data_dir() / "paper.sqlite3")
    repository.initialize()
    return repository


def _safe_limit(raw: str | None, *, default: int = 250, maximum: int = 500) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1:
        raise ValueError("limit must be positive")
    return min(value, maximum)


def _safe_offset(raw: str | None) -> int:
    if raw is None:
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("offset must be an integer") from exc
    if value < 0:
        raise ValueError("offset must be non-negative")
    return value


def _directional_bucket_state(payload: dict[str, object]) -> str:
    reason = str(payload.get("reason", "NO_RUNTIME_EVIDENCE"))
    if reason == "OFFICIAL_PTB_UNAVAILABLE":
        return "PTB_UNAVAILABLE"
    if reason in {"FEATURES_UNAVAILABLE", "FEATURE_HISTORY_WARMING"}:
        return "FEATURE_HISTORY_WARMING"
    if reason in {"MODEL_UNAVAILABLE", "CALIBRATION_NOT_READY"}:
        return str(payload.get("model_state", "TRAINING_CORPUS_REQUIRED"))
    if payload.get("action") == "TRADE":
        return "PAPER_POSITION_OPEN"
    if payload:
        return "ABSTAIN"
    return "WAITING_FOR_BOUNDARY"


def _trade_as_dict(item: object) -> dict[str, object]:
    from direction_engine_v3.storage import PaperTradeSnapshot

    if not isinstance(item, PaperTradeSnapshot):
        raise TypeError("item must be PaperTradeSnapshot")
    return {
        "trade_id": item.trade_id,
        "decision_id": item.decision_id,
        "strategy": item.strategy,
        "asset": item.asset,
        "horizon": item.horizon,
        "condition_id": item.condition_id,
        "side": item.side,
        "status": item.status,
        "label": item.label,
        "payload": dict(item.payload),
        "observed_at": item.observed_at.isoformat(),
    }
