from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from direction_engine_v3.market_data import (
    BufferOverflowError,
    ConnectionState,
    EventBuffer,
    FreshnessPolicy,
    MarketDataStaleError,
    OverflowPolicy,
    RetryPolicy,
    SequenceGapError,
    SequenceTracker,
    SourceHealth,
    utc_from_milliseconds,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_unix_milliseconds_preserve_utc() -> None:
    assert utc_from_milliseconds("1757937600123") == datetime(
        2025, 9, 15, 12, 0, 0, 123000, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "health",
    [
        SourceHealth("feed", ConnectionState.DISCONNECTED, NOW),
        SourceHealth("feed", ConnectionState.CONNECTED, NOW),
        SourceHealth("feed", ConnectionState.SUBSCRIBED, NOW),
        SourceHealth(
            "feed", ConnectionState.SUBSCRIBED, NOW, NOW, NOW, resync_required=True
        ),
        SourceHealth(
            "feed",
            ConnectionState.SUBSCRIBED,
            NOW,
            NOW - timedelta(seconds=11),
            NOW,
        ),
        SourceHealth(
            "feed",
            ConnectionState.SUBSCRIBED,
            NOW,
            NOW,
            NOW - timedelta(seconds=6),
        ),
    ],
)
def test_missing_unsubscribed_stale_or_gapped_sources_fail_closed(health: SourceHealth) -> None:
    with pytest.raises(MarketDataStaleError):
        health.require_fresh(
            FreshnessPolicy(
                max_source_age=timedelta(seconds=5),
                max_transport_silence=timedelta(seconds=10),
            )
        )


def test_subscribed_source_requires_both_freshness_clocks() -> None:
    health = SourceHealth(
        "feed",
        ConnectionState.SUBSCRIBED,
        NOW,
        NOW - timedelta(seconds=2),
        NOW - timedelta(seconds=3),
    )
    health.require_fresh(
        FreshnessPolicy(
            max_source_age=timedelta(seconds=5),
            max_transport_silence=timedelta(seconds=10),
        )
    )


def test_sequence_tracker_requires_snapshot_and_detects_gap() -> None:
    tracker = SequenceTracker()
    with pytest.raises(SequenceGapError):
        tracker.apply(10, 11)
    tracker.restore_snapshot(9)
    assert tracker.apply(10, 11)
    assert not tracker.apply(10, 11)
    with pytest.raises(SequenceGapError):
        tracker.apply(13, 14)
    assert tracker.resync_required
    with pytest.raises(SequenceGapError):
        tracker.apply(12, 12)


def test_retry_policy_is_bounded_and_deterministic_with_seeded_rng() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=1,
        maximum_delay_seconds=4,
        jitter_ratio=0,
    )
    assert [policy.delay(attempt, Random(1)) for attempt in range(1, 5)] == [1, 2, 4, 4]


def test_bounded_buffer_overflow_semantics_are_explicit() -> None:
    lossless = EventBuffer[int](capacity=1, overflow_policy=OverflowPolicy.FAIL_CLOSED)
    lossless.put_nowait(1)
    with pytest.raises(BufferOverflowError):
        lossless.put_nowait(2)

    latest = EventBuffer[int](capacity=1, overflow_policy=OverflowPolicy.LATEST_ONLY)
    latest.put_nowait(1)
    latest.put_nowait(2)
    assert latest.dropped_count == 1
    assert latest.size == 1
