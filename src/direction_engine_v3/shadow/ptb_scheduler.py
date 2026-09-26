"""Boundary-prioritized PTB establishment for the shadow runtime."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import aiohttp

from direction_engine_v3.domain._validation import require_utc
from direction_engine_v3.market_data import (
    HORIZON_DURATIONS,
    SUPPORTED_MARKET_BUCKETS,
    MarketBucket,
    MarketDataError,
    MarketDiscovery,
    PriceToBeatRecord,
    ReferenceFreshnessPolicy,
    window_containing,
)
from direction_engine_v3.market_data.official_runtime import (
    OfficialPriceToBeatService,
    PriceToBeatResolution,
)
from direction_engine_v3.shadow.storage import SQLiteShadowRepository

_EVENT_TYPE = "PTB_BOUNDARY_SCHEDULER"


class SchedulerClock(Protocol):
    def utc_now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


class PTBDiscoveryClient(Protocol):
    async def discover_market(
        self, bucket: MarketBucket, *, window_start: datetime
    ) -> MarketDiscovery: ...


@dataclass(frozen=True, slots=True)
class PTBBoundarySchedulerConfig:
    tick_seconds: float = 1.0
    settle_delay_seconds: float = 1.5
    retry_interval_seconds: float = 2.0
    max_concurrency: int = 12

    def __post_init__(self) -> None:
        if self.tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        if self.settle_delay_seconds < 0:
            raise ValueError("settle_delay_seconds must be non-negative")
        if self.retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be positive")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")


@dataclass(frozen=True, slots=True)
class PTBBoundarySchedulerResult:
    bucket: MarketBucket
    window_start: datetime
    window_end: datetime
    scheduler_detected_at: datetime
    first_attempt_at: datetime | None
    last_attempt_at: datetime | None
    boundary_detection_lag_ms: int
    attempt_count: int
    scheduler_outcome: str
    ptb_status: str
    ptb_reason: str
    price_to_beat: PriceToBeatRecord | None
    restored: bool
    last_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        reference = self.price_to_beat.reference if self.price_to_beat is not None else None
        boundary_delta_ms = None
        if reference is not None:
            boundary_delta_ms = int(
                abs((reference.effective_ts - self.window_start).total_seconds()) * 1000
            )
        return {
            "asset": self.bucket.asset.value,
            "horizon": self.bucket.horizon.value,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "scheduler_detected_at": self.scheduler_detected_at.isoformat(),
            "first_attempt_at": self.first_attempt_at.isoformat()
            if self.first_attempt_at is not None
            else None,
            "last_attempt_at": self.last_attempt_at.isoformat()
            if self.last_attempt_at is not None
            else None,
            "boundary_detection_lag_ms": self.boundary_detection_lag_ms,
            "attempt_count": self.attempt_count,
            "scheduler_outcome": self.scheduler_outcome,
            "ptb_status": self.ptb_status,
            "ptb_reason": self.ptb_reason,
            "ptb_value": str(self.price_to_beat.value)
            if self.price_to_beat is not None
            else None,
            "official_source": reference.source if reference is not None else None,
            "effective_ts": reference.effective_ts.isoformat() if reference is not None else None,
            "source_ts": reference.source_ts.isoformat() if reference is not None else None,
            "recv_ts": reference.recv_ts.isoformat() if reference is not None else None,
            "boundary_delta_ms": boundary_delta_ms,
            "persistence_id": self.price_to_beat.persistence_id
            if self.price_to_beat is not None
            else None,
            "restored": self.restored,
            "last_error": self.last_error,
            "real_order_submission": False,
        }


@dataclass(slots=True)
class _AttemptState:
    detected_at: datetime
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempt_count: int = 0


class PTBBoundaryScheduler:
    """Establish PTB at canonical boundaries independently from heavy cycles."""

    def __init__(
        self,
        *,
        discovery_client: PTBDiscoveryClient,
        official_ptb: OfficialPriceToBeatService,
        shadow_repository: SQLiteShadowRepository,
        evidence_window_id: str,
        clock: SchedulerClock,
        policy: ReferenceFreshnessPolicy,
        config: PTBBoundarySchedulerConfig | None = None,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self._discovery_client = discovery_client
        self._official_ptb = official_ptb
        self._shadow_repository = shadow_repository
        self._evidence_window_id = evidence_window_id
        self._clock = clock
        self._policy = policy
        self._config = config or PTBBoundarySchedulerConfig()
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(self._config.max_concurrency)
        self._states: dict[tuple[MarketBucket, datetime], _AttemptState] = {}
        self._tasks: dict[tuple[MarketBucket, datetime], asyncio.Task[None]] = {}

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_due_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._config.tick_seconds)
            except TimeoutError:
                continue
        await self.cancel()

    async def run_due_once(self) -> None:
        self._raise_finished_failures()
        now = self._clock.utc_now()
        self._prune_old_state(now)
        for bucket in SUPPORTED_MARKET_BUCKETS:
            window = window_containing(bucket, now)
            key = (bucket, window.start)
            if key in self._states or key in self._tasks:
                continue
            due_at = window.start + timedelta(seconds=self._config.settle_delay_seconds)
            deadline = window.start + self._policy.boundary_tolerance
            if now < due_at:
                continue
            detected_at = now
            state = _AttemptState(detected_at=detected_at)
            self._states[key] = state
            if now > deadline:
                self._record_result(
                    PTBBoundarySchedulerResult(
                        bucket=bucket,
                        window_start=window.start,
                        window_end=window.end,
                        scheduler_detected_at=detected_at,
                        first_attempt_at=None,
                        last_attempt_at=None,
                        boundary_detection_lag_ms=_lag_ms(detected_at, window.start),
                        attempt_count=0,
                        scheduler_outcome="MISSED_BOUNDARY",
                        ptb_status="PTB_UNAVAILABLE",
                        ptb_reason="MISSED_BOUNDARY",
                        price_to_beat=None,
                        restored=False,
                    )
                )
                continue
            self._tasks[key] = asyncio.create_task(
                self._run_bucket_window(bucket, window.start, window.end, state),
                name=(
                    "ptb-boundary-"
                    f"{bucket.asset.value}-{bucket.horizon.value}-"
                    f"{int(window.start.timestamp())}"
                ),
            )
        self._raise_finished_failures()

    async def wait_idle(self, *, timeout_seconds: float = 5.0) -> None:
        if not self._tasks:
            return
        await asyncio.wait_for(asyncio.gather(*self._tasks.values()), timeout=timeout_seconds)
        self._raise_finished_failures()

    async def cancel(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _run_bucket_window(
        self,
        bucket: MarketBucket,
        window_start: datetime,
        window_end: datetime,
        state: _AttemptState,
    ) -> None:
        key = (bucket, window_start)
        deadline = window_start + self._policy.boundary_tolerance
        last_result: PriceToBeatResolution | None = None
        last_error: str | None = None
        try:
            while True:
                now = self._clock.utc_now()
                if now > deadline:
                    self._record_deadline_result(
                        bucket,
                        window_start,
                        window_end,
                        state,
                        last_result,
                        last_error,
                    )
                    return
                state.attempt_count += 1
                state.last_attempt_at = now
                if state.first_attempt_at is None:
                    state.first_attempt_at = now
                try:
                    async with self._semaphore:
                        discovery = await self._discovery_client.discover_market(
                            bucket, window_start=window_start
                        )
                        observed_at = self._clock.utc_now()
                        resolution = await self._official_ptb.resolve(
                            discovery, observed_at=observed_at
                        )
                except Exception as exc:
                    if not _is_expected_scheduler_failure(exc):
                        raise
                    last_error = f"{type(exc).__name__}:{exc}"
                    resolution = None

                if resolution is not None:
                    last_result = resolution
                    if resolution.ready:
                        self._record_result(
                            PTBBoundarySchedulerResult(
                                bucket=bucket,
                                window_start=window_start,
                                window_end=window_end,
                                scheduler_detected_at=state.detected_at,
                                first_attempt_at=state.first_attempt_at,
                                last_attempt_at=state.last_attempt_at,
                                boundary_detection_lag_ms=_lag_ms(
                                    state.detected_at, window_start
                                ),
                                attempt_count=state.attempt_count,
                                scheduler_outcome="PTB_READY",
                                ptb_status=resolution.ptb_status,
                                ptb_reason=resolution.reason,
                                price_to_beat=resolution.price_to_beat,
                                restored=resolution.restored,
                                last_error=last_error,
                            )
                        )
                        return

                retry_at = self._clock.utc_now() + timedelta(
                    seconds=self._config.retry_interval_seconds
                )
                if retry_at > deadline:
                    self._record_deadline_result(
                        bucket,
                        window_start,
                        window_end,
                        state,
                        last_result,
                        last_error,
                    )
                    return
                await self._sleep(self._config.retry_interval_seconds)
        finally:
            self._tasks.pop(key, None)

    def _record_deadline_result(
        self,
        bucket: MarketBucket,
        window_start: datetime,
        window_end: datetime,
        state: _AttemptState,
        last_result: PriceToBeatResolution | None,
        last_error: str | None,
    ) -> None:
        ptb_status = last_result.ptb_status if last_result is not None else "PTB_UNAVAILABLE"
        ptb_reason = (
            last_result.reason
            if last_result is not None
            else "DISCOVERY_UNAVAILABLE"
            if last_error is not None
            else "ATTEMPTS_EXHAUSTED"
        )
        self._record_result(
            PTBBoundarySchedulerResult(
                bucket=bucket,
                window_start=window_start,
                window_end=window_end,
                scheduler_detected_at=state.detected_at,
                first_attempt_at=state.first_attempt_at,
                last_attempt_at=state.last_attempt_at,
                boundary_detection_lag_ms=_lag_ms(state.detected_at, window_start),
                attempt_count=state.attempt_count,
                scheduler_outcome="ATTEMPTS_EXHAUSTED",
                ptb_status=ptb_status,
                ptb_reason=ptb_reason,
                price_to_beat=last_result.price_to_beat
                if last_result is not None and last_result.ready
                else None,
                restored=last_result.restored if last_result is not None else False,
                last_error=last_error,
            )
        )

    def _record_result(self, result: PTBBoundarySchedulerResult) -> None:
        event_id = (
            f"{self._evidence_window_id}:ptb-scheduler:"
            f"{result.bucket.asset.value}:{result.bucket.horizon.value}:"
            f"{int(result.window_start.timestamp())}"
        )
        self._shadow_repository.append_event(
            event_id=event_id,
            window_id=self._evidence_window_id,
            event_type=_EVENT_TYPE,
            bucket_key=f"{result.bucket.asset.value}-{result.bucket.horizon.value}",
            payload=result.as_dict(),
            observed_at=self._clock.utc_now(),
        )

    def _raise_finished_failures(self) -> None:
        for key, task in list(self._tasks.items()):
            if not task.done():
                continue
            self._tasks.pop(key, None)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc

    def _prune_old_state(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=3)
        for key in tuple(self._states):
            bucket, window_start = key
            if window_start + HORIZON_DURATIONS[bucket.horizon] < cutoff:
                self._states.pop(key, None)


def _lag_ms(now: datetime, window_start: datetime) -> int:
    require_utc("now", now)
    require_utc("window_start", window_start)
    return int((now - window_start).total_seconds() * 1000)


def _is_expected_scheduler_failure(exc: Exception) -> bool:
    return isinstance(exc, (MarketDataError, TimeoutError, aiohttp.ClientError))
