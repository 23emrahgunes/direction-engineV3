from datetime import UTC, datetime, timedelta

from direction_engine_v3.adapters.binance import parse_depth_top
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import (
    ConnectionState,
    FreshnessPolicy,
    SequenceTracker,
    SourceHealth,
)


def test_normalized_depth_event_passes_sequence_and_freshness_gates() -> None:
    recv_ts = datetime(2026, 9, 15, 12, 0, 0, 200000, tzinfo=UTC)
    top, first_update_id = parse_depth_top(
        {
            "e": "depthUpdate",
            "E": 1789473600100,
            "s": "SOLUSDT",
            "U": 501,
            "u": 502,
            "b": [["200.10", "4"]],
            "a": [["200.20", "5"]],
        },
        asset=Asset.SOL,
        recv_ts=recv_ts,
        normalized_ts=recv_ts,
        recv_monotonic_ns=5_000,
    )
    tracker = SequenceTracker()
    tracker.restore_snapshot(500)
    assert tracker.apply(first_update_id, top.update_id)
    SourceHealth(
        source=top.lineage.source,
        state=ConnectionState.SUBSCRIBED,
        observed_at=recv_ts + timedelta(milliseconds=50),
        last_transport_recv_ts=top.lineage.recv_ts,
        last_source_event_ts=top.lineage.source_ts,
        resync_required=tracker.resync_required,
    ).require_fresh(
        FreshnessPolicy(
            max_source_age=timedelta(seconds=1),
            max_transport_silence=timedelta(seconds=1),
        )
    )
