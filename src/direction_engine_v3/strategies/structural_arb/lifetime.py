"""Contiguous structural-opportunity lifetime tracking."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.strategies.structural_arb.scanner import StructuralOpportunity


@dataclass(frozen=True, slots=True)
class OpportunityWindow:
    key: str
    first_seen_at: datetime
    last_seen_at: datetime
    peak_net_profit: Decimal
    observations: int
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_text("key", self.key)
        require_utc("first_seen_at", self.first_seen_at)
        require_utc("last_seen_at", self.last_seen_at)
        require_decimal("peak_net_profit", self.peak_net_profit)
        if self.observations < 1:
            raise ValueError("observations must be positive")
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("first_seen_at cannot follow last_seen_at")
        if self.closed_at is not None:
            require_utc("closed_at", self.closed_at)
            if self.closed_at < self.last_seen_at:
                raise ValueError("closed_at cannot precede last_seen_at")


@dataclass(slots=True)
class OpportunityLifetimeTracker:
    current: OpportunityWindow | None = None

    def observe(self, opportunity: StructuralOpportunity) -> OpportunityWindow:
        key = f"{opportunity.condition_id}:{opportunity.action.value}"
        if self.current is None or self.current.key != key or self.current.closed_at is not None:
            self.current = OpportunityWindow(
                key=key,
                first_seen_at=opportunity.observed_at,
                last_seen_at=opportunity.observed_at,
                peak_net_profit=opportunity.expected_net_profit,
                observations=1,
            )
        else:
            self.current = OpportunityWindow(
                key=key,
                first_seen_at=self.current.first_seen_at,
                last_seen_at=opportunity.observed_at,
                peak_net_profit=max(
                    self.current.peak_net_profit, opportunity.expected_net_profit
                ),
                observations=self.current.observations + 1,
            )
        return self.current

    def close(self, closed_at: datetime) -> OpportunityWindow | None:
        require_utc("closed_at", closed_at)
        if self.current is None or self.current.closed_at is not None:
            return self.current
        self.current = OpportunityWindow(
            key=self.current.key,
            first_seen_at=self.current.first_seen_at,
            last_seen_at=self.current.last_seen_at,
            peak_net_profit=self.current.peak_net_profit,
            observations=self.current.observations,
            closed_at=closed_at,
        )
        return self.current
