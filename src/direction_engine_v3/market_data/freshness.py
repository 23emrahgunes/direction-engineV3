"""Fail-closed source-health and freshness evaluation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.market_data.errors import MarketDataStaleError


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    SUBSCRIBED = "SUBSCRIBED"


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Maximum source-event and transport-silence ages."""

    max_source_age: timedelta
    max_transport_silence: timedelta

    def __post_init__(self) -> None:
        if self.max_source_age <= timedelta(0):
            raise ValueError("max_source_age must be positive")
        if self.max_transport_silence <= timedelta(0):
            raise ValueError("max_transport_silence must be positive")


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Health evidence that keeps connectivity distinct from data freshness."""

    source: str
    state: ConnectionState
    observed_at: datetime
    last_transport_recv_ts: datetime | None = None
    last_source_event_ts: datetime | None = None
    resync_required: bool = False

    def __post_init__(self) -> None:
        require_text("source", self.source)
        if not isinstance(self.state, ConnectionState):
            raise TypeError("state must be ConnectionState")
        require_utc("observed_at", self.observed_at)
        if self.last_transport_recv_ts is not None:
            require_utc("last_transport_recv_ts", self.last_transport_recv_ts)
        if self.last_source_event_ts is not None:
            require_utc("last_source_event_ts", self.last_source_event_ts)
        if not isinstance(self.resync_required, bool):
            raise TypeError("resync_required must be a bool")

    def require_fresh(self, policy: FreshnessPolicy) -> None:
        """Raise unless subscription, sequence, transport, and source time are all healthy."""

        if self.state is not ConnectionState.SUBSCRIBED:
            raise MarketDataStaleError(f"{self.source}: source is not subscribed")
        if self.resync_required:
            raise MarketDataStaleError(f"{self.source}: snapshot resynchronization required")
        if self.last_transport_recv_ts is None or self.last_source_event_ts is None:
            raise MarketDataStaleError(f"{self.source}: freshness timestamps are missing")
        transport_age = self.observed_at - self.last_transport_recv_ts
        source_age = self.observed_at - self.last_source_event_ts
        if transport_age < timedelta(0) or source_age < timedelta(0):
            raise MarketDataStaleError(f"{self.source}: future timestamp is unverifiable")
        if transport_age > policy.max_transport_silence:
            raise MarketDataStaleError(f"{self.source}: transport is stale")
        if source_age > policy.max_source_age:
            raise MarketDataStaleError(f"{self.source}: source event is stale")
