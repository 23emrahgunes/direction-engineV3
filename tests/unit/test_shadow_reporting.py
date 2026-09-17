from datetime import UTC, datetime

from direction_engine_v3.shadow import (
    AWSIdentityEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
    SQLiteShadowRepository,
    build_shadow_summary,
    write_reports,
)


def _window() -> EvidenceWindow:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fingerprint = EvidenceFingerprint(
        code_commit="abc123",
        strategy_version="strategy",
        model_version="model",
        calibration_version="calibration",
        feature_schema_version="features",
        risk_policy_version="risk",
        router_policy_version="router",
        execution_policy_version="execution",
        config_hash="config",
        artifact_hashes=(),
    )
    return EvidenceWindow(
        "window-1",
        now,
        fingerprint,
        AWSIdentityEvidence("user", "account", "arn:aws:iam::123456789012:user/test", now),
    )


def test_shadow_repository_is_idempotent_and_append_only(tmp_path) -> None:
    repository = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    window = _window()
    repository.initialize()
    repository.save_window_once(
        window_id=window.window_id,
        payload=window.as_dict(),
        started_at=window.started_at,
    )
    repository.save_window_once(
        window_id=window.window_id,
        payload=window.as_dict(),
        started_at=window.started_at,
    )
    repository.append_event(
        event_id="event-1",
        window_id=window.window_id,
        event_type="COLLECTOR_STARTED",
        bucket_key=None,
        payload={"app_mode": "PAPER"},
        observed_at=window.started_at,
    )
    repository.append_event(
        event_id="event-1",
        window_id=window.window_id,
        event_type="COLLECTOR_STARTED",
        bucket_key=None,
        payload={"app_mode": "PAPER"},
        observed_at=window.started_at,
    )

    assert repository.latest_window_payload() is not None
    assert repository.event_counts() == {"COLLECTOR_STARTED": 1}


def test_shadow_repository_latest_events_returns_newest_first(tmp_path) -> None:
    repository = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="old",
        window_id="window",
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        payload={"version": "old"},
        observed_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    repository.append_event(
        event_id="new",
        window_id="window",
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        payload={"version": "new"},
        observed_at=datetime(2026, 9, 15, 12, 1, tzinfo=UTC),
    )

    events = repository.latest_events(
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        limit=10,
    )

    assert [event["event_id"] for event in events] == ["new", "old"]


def test_write_reports_is_deterministic_and_machine_readable(tmp_path) -> None:
    summary = build_shadow_summary(evidence_window=_window(), generated_at=_window().started_at)

    json_path, md_path = write_reports(summary, tmp_path)

    payload = json_path.read_text(encoding="utf-8")
    assert payload.count('"promotion_state": "INSUFFICIENT_SAMPLE"') == 13
    assert payload.count('"asset":') == 12
    assert "V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING" in md_path.read_text(
        encoding="utf-8"
    )
