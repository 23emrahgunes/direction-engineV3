"""Read-only dashboard snapshot assembly for V3.13."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

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
from direction_engine_v3.shadow.storage import ShadowStorageUnavailable, SQLiteShadowRepository
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


def build_paper_performance(*, strategy: str = "DIRECTIONAL_EDGE") -> dict[str, object]:
    repository = _paper_repository()
    return repository.performance(strategy=strategy)


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
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3", read_only=True)
    event_counts: dict[str, int]
    try:
        event_counts = shadow.event_counts()
    except ShadowStorageUnavailable as exc:
        event_counts = {"SHADOW_STATUS_UNAVAILABLE": 1}
        return {
            "status": "DATABASE_NOT_INITIALIZED",
            "reason": str(exc),
            "real_order_submission": False,
            "event_counts": event_counts,
            "paper_database": str(data_dir / "paper.sqlite3"),
        }
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
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3", read_only=True)
    try:
        directional_events = shadow.latest_events(event_type="STRATEGY_EVALUATION", limit=500)
        pipeline_events = shadow.latest_events(event_type="MARKET_DATA_PIPELINE", limit=500)
    except ShadowStorageUnavailable as exc:
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "status": "DATABASE_NOT_INITIALIZED",
            "reason": str(exc),
            "real_order_submission": False,
            "buckets": [],
        }
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
        audit_payload = payload.get("decision_audit")
        decision_audit = dict(audit_payload) if isinstance(audit_payload, dict) else {}
        signal_obj = decision_audit.get("signal")
        pricing_obj = decision_audit.get("pricing")
        edge_obj = decision_audit.get("edge")
        audit_signal = (
            dict(signal_obj)
            if isinstance(signal_obj, dict)
            else {}
        )
        audit_pricing = (
            dict(pricing_obj)
            if isinstance(pricing_obj, dict)
            else {}
        )
        audit_edge = (
            dict(edge_obj)
            if isinstance(edge_obj, dict)
            else {}
        )
        pipeline_payload_obj = latest_pipeline["payload"] if latest_pipeline is not None else {}
        pipeline_payload = (
            dict(pipeline_payload_obj) if isinstance(pipeline_payload_obj, dict) else {}
        )
        strategy_observed_at = latest["observed_at"] if latest is not None else None
        pipeline_observed_at = (
            latest_pipeline["observed_at"] if latest_pipeline is not None else None
        )
        latest_observed_at = pipeline_payload.get("latest_observed_at", pipeline_observed_at)
        trades = _paper_trades_for_directional_bucket(data_dir, bucket)
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
                "decision_audit": decision_audit,
                "audit_version": decision_audit.get("version"),
                "first_failing_gate": decision_audit.get("first_failing_gate"),
                "final_decision": decision_audit.get("final_decision", payload.get("action")),
                "final_reason": decision_audit.get("final_reason", payload.get("reason")),
                "signal_stability": audit_signal.get("signal_stability_actual"),
                "minimum_signal_stability": audit_signal.get("signal_stability_minimum"),
                "flip_rate": audit_signal.get("flip_rate_actual"),
                "maximum_flip_rate": audit_signal.get("flip_rate_maximum"),
                "counterfactual_net_edge": audit_edge.get(
                    "counterfactual_net_edge",
                    audit_pricing.get("counterfactual_net_edge"),
                ),
                "counterfactual_edge_margin": audit_edge.get(
                    "counterfactual_edge_margin",
                    audit_pricing.get("counterfactual_edge_margin"),
                ),
                "counterfactual_pricing_available": audit_pricing.get(
                    "counterfactual_pricing_available"
                ),
                "current_losing_streak": _execution_field(
                    payload, "current_losing_streak"
                ),
                "cooldown_active": _execution_field(payload, "cooldown_active"),
                "cooldown_until": _execution_field(payload, "cooldown_until"),
                "cooldown_remaining_seconds": _execution_field(
                    payload, "cooldown_remaining_seconds"
                ),
                "last_successful_settlement_at": _execution_field(
                    payload, "last_successful_settlement_at"
                ),
                "risk_reasons": _execution_field(payload, "risk_reasons", ()),
                "risk_brake_active": _execution_field(
                    payload, "risk_brake_active", False
                ),
                "risk_brake_reason": _execution_field(payload, "risk_brake_reason"),
                "paper_current_equity": _execution_field(payload, "paper_current_equity"),
                "open_cost_basis": _execution_field(payload, "open_cost_basis"),
                "open_trade_count": _execution_field(payload, "open_trade_count"),
                "same_asset_open_count": _execution_field(
                    payload, "same_asset_open_count"
                ),
                "recent_directional_win_rate": _execution_field(
                    payload, "recent_directional_win_rate"
                ),
                "recent_directional_pnl": _execution_field(
                    payload, "recent_directional_pnl"
                ),
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


def build_directional_decision_audit(*, hours: str | None = None) -> dict[str, object]:
    lookback_hours = _safe_hours(hours)
    generated_at = datetime.now(UTC)
    since = generated_at - timedelta(hours=lookback_hours)
    data_dir = runtime_data_dir()
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3", read_only=True)
    try:
        events = shadow.events_since(
            event_type="STRATEGY_EVALUATION",
            observed_at=since,
            limit=50_000,
        )
    except ShadowStorageUnavailable as exc:
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "status": "DATABASE_NOT_INITIALIZED",
            "reason": str(exc),
            "real_order_submission": False,
        }
    directional_events = [
        event
        for event in events
        if isinstance(event.get("payload"), dict)
        and dict(cast(dict[str, object], event["payload"])).get("strategy")
        == "DIRECTIONAL_EDGE"
    ]
    reason_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    gate_counts: dict[str, int] = {}
    by_bucket: dict[str, list[dict[str, object]]] = {}
    stability_values: list[Decimal] = []
    flip_values: list[Decimal] = []
    stability_distance_values: list[Decimal] = []
    flip_distance_values: list[Decimal] = []
    edge_distance_values: list[Decimal] = []
    audit_count = 0
    for event in directional_events:
        payload = cast(dict[str, object], event["payload"])
        action = str(payload.get("action", "UNKNOWN"))
        reason = str(payload.get("reason", "UNKNOWN"))
        action_counts[action] = action_counts.get(action, 0) + 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        bucket_key = str(event.get("bucket_key") or "UNKNOWN")
        audit_obj = payload.get("decision_audit")
        if isinstance(audit_obj, dict):
            audit = dict(audit_obj)
            audit_count += 1
            by_bucket.setdefault(bucket_key, []).append(audit)
            gate = audit.get("first_failing_gate")
            if gate is not None:
                gate_key = str(gate)
                gate_counts[gate_key] = gate_counts.get(gate_key, 0) + 1
            signal = audit.get("signal")
            if isinstance(signal, dict):
                _append_decimal(stability_values, signal.get("signal_stability_actual"))
                _append_decimal(flip_values, signal.get("flip_rate_actual"))
                _append_decimal(
                    stability_distance_values,
                    signal.get("signal_stability_minus_minimum"),
                )
                _append_decimal(
                    flip_distance_values,
                    signal.get("maximum_flip_rate_minus_actual"),
                )
            edge = audit.get("edge")
            if isinstance(edge, dict):
                _append_decimal(edge_distance_values, edge.get("counterfactual_edge_margin"))
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "status": "DIRECTIONAL_DECISION_AUDIT_READY",
        "version": "DIRECTIONAL_DECISION_AUDIT_V1",
        "generated_at": generated_at.isoformat(),
        "window": {"hours": lookback_hours, "since": since.isoformat()},
        "real_order_submission": False,
        "total_directional_evaluations": len(directional_events),
        "action_counts": action_counts,
        "reason_counts": reason_counts,
        "first_failing_gate_counts": gate_counts,
        "audit_payload_coverage": {
            "events_with_audit": audit_count,
            "total_directional_evaluations": len(directional_events),
        },
        "signal_stability_distribution": _distribution(stability_values),
        "flip_rate_distribution": _distribution(flip_values),
        "threshold_distance_distributions": {
            "signal_stability_minus_minimum": _distribution(stability_distance_values),
            "maximum_flip_rate_minus_actual": _distribution(flip_distance_values),
            "counterfactual_edge_margin": _distribution(edge_distance_values),
        },
        "same_asset_cross_horizon": _same_asset_cross_horizon(by_bucket),
    }


def _paper_trades_for_directional_bucket(
    data_dir: Path, bucket: object
) -> tuple[object, ...]:
    from direction_engine_v3.market_data import MarketBucket

    if not isinstance(bucket, MarketBucket):
        raise TypeError("bucket must be MarketBucket")
    paper_path = data_dir / "paper.sqlite3"
    if not paper_path.exists():
        return ()
    repository = SQLitePaperRepository(paper_path)
    try:
        return repository.trades(
            asset=bucket.asset.value,
            horizon=bucket.horizon.value,
            strategy="DIRECTIONAL_EDGE",
            limit=1,
        )
    except Exception:
        return ()


def _safe_hours(raw: str | None, *, default: int = 24, maximum: int = 72) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("hours must be an integer") from exc
    if value < 1:
        raise ValueError("hours must be positive")
    return min(value, maximum)


def _append_decimal(target: list[Decimal], raw: object) -> None:
    if raw is None:
        return
    try:
        target.append(Decimal(str(raw)))
    except Exception:
        return


def _distribution(values: list[Decimal]) -> dict[str, object]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count,
        "min": str(ordered[0]),
        "p10": str(_percentile(ordered, Decimal("0.10"))),
        "p25": str(_percentile(ordered, Decimal("0.25"))),
        "median": str(_percentile(ordered, Decimal("0.50"))),
        "p75": str(_percentile(ordered, Decimal("0.75"))),
        "p90": str(_percentile(ordered, Decimal("0.90"))),
        "max": str(ordered[-1]),
        "mean": str(sum(ordered, Decimal("0")) / Decimal(count)),
    }


def _percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if len(values) == 1:
        return values[0]
    rank = quantile * Decimal(len(values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    fraction = rank - Decimal(lower)
    return values[lower] + (values[upper] - values[lower]) * fraction


def _same_asset_cross_horizon(
    by_bucket: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for asset in SUPPORTED_ASSETS:
        rows: dict[str, object] = {}
        stability_values: list[str | None] = []
        flip_values: list[str | None] = []
        for horizon in SUPPORTED_HORIZONS:
            bucket_key = f"{asset}-{horizon}"
            latest = by_bucket.get(bucket_key, [None])[0]
            signal = latest.get("signal") if isinstance(latest, dict) else None
            temporal = latest.get("temporal_state") if isinstance(latest, dict) else None
            signal_payload = signal if isinstance(signal, dict) else {}
            temporal_payload = temporal if isinstance(temporal, dict) else {}
            stability = cast(object | None, signal_payload.get("signal_stability_actual"))
            flip = cast(object | None, signal_payload.get("flip_rate_actual"))
            stability_text = None if stability is None else str(stability)
            flip_text = None if flip is None else str(flip)
            stability_values.append(stability_text)
            flip_values.append(flip_text)
            rows[str(horizon)] = {
                "signal_stability": stability_text,
                "flip_rate": flip_text,
                "reference_sample_count": temporal_payload.get("reference_sample_count"),
                "unique_reference_source_timestamp_count": temporal_payload.get(
                    "unique_reference_source_timestamp_count"
                ),
                "near_duplicate_reference_count": temporal_payload.get(
                    "near_duplicate_reference_count"
                ),
            }
        present_stability = [item for item in stability_values if item is not None]
        present_flip = [item for item in flip_values if item is not None]
        result[str(asset)] = {
            "buckets": rows,
            "same_signal_stability_values": (
                len(set(present_stability)) == 1 if len(present_stability) > 1 else None
            ),
            "same_flip_rate_values": (
                len(set(present_flip)) == 1 if len(present_flip) > 1 else None
            ),
            "shared_state_evidence": "asset_level_temporal_state_diagnostics",
        }
    return result


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
    execution = payload.get("directional_execution")
    execution_payload = execution if isinstance(execution, dict) else {}
    if execution_payload.get("risk_approved") is False:
        reasons = execution_payload.get("risk_reasons")
        if isinstance(reasons, (list, tuple)) and any(
            str(item)
            in {
                "PAPER_DRAWDOWN_BRAKE_ACTIVE",
                "OPEN_EXPOSURE_LIMIT",
                "MAXIMUM_PAPER_POSITIONS",
                "OVERLAPPING_ASSET_LIMIT",
                "POOR_RECENT_PAPER_PERFORMANCE",
                "BASELINE_RESEARCH_STAKE_CAP",
            }
            for item in reasons
        ):
            return "RISK_BRAKE_ACTIVE"
        if (
            isinstance(reasons, (list, tuple))
            and "CONSECUTIVE_LOSS_COOLDOWN_ACTIVE" in reasons
        ):
            return "COOLDOWN_ACTIVE"
        return "RISK_REJECTED"
    if payload.get("action") == "TRADE":
        fill_status = execution_payload.get("paper_fill_status")
        paper_trade_id = execution_payload.get("paper_trade_id")
        if paper_trade_id is not None or fill_status in {"FILLED", "ACKNOWLEDGED"}:
            return "PAPER_POSITION_OPEN"
        return "TRADE_PENDING_EXECUTION"
    if payload:
        return "ABSTAIN"
    return "WAITING_FOR_BOUNDARY"


def _execution_field(
    payload: dict[str, object], key: str, default: object | None = None
) -> object | None:
    execution = payload.get("directional_execution")
    if isinstance(execution, dict) and key in execution:
        return cast(object, execution[key])
    return payload.get(key, default)


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
