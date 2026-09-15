"""Read-only dashboard snapshot assembly for V3.13."""

from dataclasses import dataclass
from datetime import UTC, datetime

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
