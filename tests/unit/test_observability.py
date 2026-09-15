from datetime import UTC, datetime

import pytest

from direction_engine_v3.observability import (
    ComponentHealth,
    HealthStatus,
    MetricsSnapshot,
    ReadinessReport,
    build_log_event,
    redact_mapping,
)


def test_redaction_removes_secret_like_values() -> None:
    payload = redact_mapping(
        {
            "POLYMARKET_API_SECRET": "secret-value",
            "nested": {"private_key": "key-value", "safe": "visible"},
        }
    )

    assert payload["POLYMARKET_API_SECRET"] == "<redacted>"
    assert payload["nested"] == {"private_key": "<redacted>", "safe": "visible"}


def test_structured_log_event_redacts_fields() -> None:
    event = build_log_event(
        "dashboard_check",
        severity="INFO",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        fields={"api_key": "abc", "status": "ok"},
    )

    assert event.as_dict()["fields"] == {"api_key": "<redacted>", "status": "ok"}


def test_readiness_is_not_healthy_just_because_process_is_alive() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    passing = ComponentHealth("process", HealthStatus.PASSING, "ok", now)
    report = ReadinessReport(
        process_alive=passing,
        database=passing,
        data_freshness=passing,
        model_artifacts=passing,
        trading_readiness=ComponentHealth("trading", HealthStatus.FAILING, "live off", now),
    )

    assert report.ready is False


def test_metrics_snapshot_rejects_live_order_counts() -> None:
    with pytest.raises(ValueError, match="LIVE orders"):
        MetricsSnapshot(datetime(2026, 1, 1, tzinfo=UTC), live_order_count=1)
