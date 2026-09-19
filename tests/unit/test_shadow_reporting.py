import sqlite3
import threading
from datetime import UTC, datetime

from direction_engine_v3.app import dashboard
from direction_engine_v3.diagnostics import paper_burnin_status
from direction_engine_v3.shadow import (
    AWSIdentityEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
    ShadowStorageBusy,
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


def test_shadow_repository_retries_temporary_writer_lock_once(tmp_path) -> None:
    repository = SQLiteShadowRepository(
        tmp_path / "shadow.sqlite3",
        busy_timeout_ms=20,
        max_busy_retries=10,
        busy_retry_sleep_seconds=0.02,
    )
    repository.initialize()
    connection = sqlite3.connect(tmp_path / "shadow.sqlite3", check_same_thread=False)
    connection.execute("BEGIN EXCLUSIVE")
    timer = threading.Timer(0.08, lambda: (connection.rollback(), connection.close()))
    timer.start()
    try:
        repository.save_window_once(
            window_id="window",
            payload={"app_mode": "PAPER"},
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    finally:
        timer.join()

    assert repository.latest_window_payload() == {"app_mode": "PAPER"}


def test_shadow_repository_reports_storage_busy_without_fake_success(tmp_path) -> None:
    repository = SQLiteShadowRepository(
        tmp_path / "shadow.sqlite3",
        busy_timeout_ms=10,
        max_busy_retries=1,
        busy_retry_sleep_seconds=0.01,
    )
    repository.initialize()
    connection = sqlite3.connect(tmp_path / "shadow.sqlite3")
    connection.execute("BEGIN EXCLUSIVE")
    try:
        try:
            repository.save_window_once(
                window_id="window",
                payload={"app_mode": "PAPER"},
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        except ShadowStorageBusy as exc:
            assert "STORAGE_BUSY" in str(exc)
        else:
            raise AssertionError("expected bounded SQLite contention failure")
    finally:
        connection.rollback()
        connection.close()
    assert repository.latest_window_payload() is None


def test_shadow_read_only_paths_do_not_create_or_initialize_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "runtime_data_dir", lambda: tmp_path)

    status = dashboard.build_shadow_status()

    assert status["status"] == "DATABASE_NOT_INITIALIZED"
    assert not (tmp_path / "shadow_evidence.sqlite3").exists()


def test_paper_burnin_status_reads_real_shadow_evidence_path_without_shadow_sqlite(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="scan-1",
        window_id="window",
        event_type="PAPER_SETTLEMENT_SCAN",
        bucket_key=None,
        payload={"last_attempted_condition": "condition-1"},
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(paper_burnin_status, "runtime_data_dir", lambda: tmp_path)

    payload = paper_burnin_status._payload()

    assert payload["recent_settlement_scans"][0]["event_id"] == "scan-1"
    assert not (tmp_path / "shadow.sqlite3").exists()


def test_write_reports_is_deterministic_and_machine_readable(tmp_path) -> None:
    summary = build_shadow_summary(evidence_window=_window(), generated_at=_window().started_at)

    json_path, md_path = write_reports(summary, tmp_path)

    payload = json_path.read_text(encoding="utf-8")
    assert payload.count('"promotion_state": "INSUFFICIENT_SAMPLE"') == 13
    assert payload.count('"asset":') == 12
    assert "V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING" in md_path.read_text(
        encoding="utf-8"
    )
