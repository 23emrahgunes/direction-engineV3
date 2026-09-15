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
) -> dict[str, object]:
    repository = _paper_repository()
    trades = repository.trades(
        asset=asset,
        horizon=horizon,
        strategy=strategy,
        side=side,
        status=status,
        win_loss=win_loss,
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
) -> dict[str, object]:
    repository = _paper_repository()
    abstains = repository.abstains(reason=reason, strategy=strategy, asset=asset, horizon=horizon)
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


def _paper_repository() -> SQLitePaperRepository:
    repository = SQLitePaperRepository(runtime_data_dir() / "paper.sqlite3")
    repository.initialize()
    return repository


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
