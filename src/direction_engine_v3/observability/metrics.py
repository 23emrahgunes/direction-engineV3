"""Small immutable metrics snapshot model for read-only dashboards."""

from dataclasses import dataclass
from datetime import datetime

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_text, require_utc


@dataclass(frozen=True, slots=True)
class FeedMetric:
    source: str
    connected: bool
    source_age_ms: int | None
    reconnect_count: int
    sequence_gap_count: int

    def __post_init__(self) -> None:
        require_text("source", self.source)
        if self.source_age_ms is not None and self.source_age_ms < 0:
            raise ValueError("source_age_ms cannot be negative")
        if self.reconnect_count < 0 or self.sequence_gap_count < 0:
            raise ValueError("feed counters cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketMetric:
    asset: Asset
    horizon: Horizon
    book_coverage: bool
    official_reference_fresh: bool
    proxy_reference_fresh: bool

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    generated_at: datetime
    feeds: tuple[FeedMetric, ...] = ()
    markets: tuple[MarketMetric, ...] = ()
    decision_count: int = 0
    abstain_count: int = 0
    risk_rejection_count: int = 0
    paper_order_count: int = 0
    live_order_count: int = 0

    def __post_init__(self) -> None:
        require_utc("generated_at", self.generated_at)
        for counter in (
            self.decision_count,
            self.abstain_count,
            self.risk_rejection_count,
            self.paper_order_count,
            self.live_order_count,
        ):
            if counter < 0:
                raise ValueError("metric counters cannot be negative")
        if self.live_order_count != 0:
            raise ValueError("V3.13/V3.14 metrics must not report LIVE orders")

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "feeds": [
                {
                    "source": feed.source,
                    "connected": feed.connected,
                    "source_age_ms": feed.source_age_ms,
                    "reconnect_count": feed.reconnect_count,
                    "sequence_gap_count": feed.sequence_gap_count,
                }
                for feed in self.feeds
            ],
            "markets": [
                {
                    "asset": market.asset.value,
                    "horizon": market.horizon.value,
                    "book_coverage": market.book_coverage,
                    "official_reference_fresh": market.official_reference_fresh,
                    "proxy_reference_fresh": market.proxy_reference_fresh,
                }
                for market in self.markets
            ],
            "decision_count": self.decision_count,
            "abstain_count": self.abstain_count,
            "risk_rejection_count": self.risk_rejection_count,
            "paper_order_count": self.paper_order_count,
            "live_order_count": self.live_order_count,
        }
