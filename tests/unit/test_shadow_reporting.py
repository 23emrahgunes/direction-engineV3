import sqlite3
import threading
import time
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


def test_shadow_repository_initializes_latest_event_indexes(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(db_path)
    repository.initialize()
    repository.initialize()

    with sqlite3.connect(db_path) as connection:
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='shadow_events'"
            ).fetchall()
        }

    assert "idx_shadow_events_type_observed" in indexes
    assert "idx_shadow_events_type_bucket_observed" in indexes


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


def test_shadow_startup_waits_out_reader_commit_contention_once(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(
        db_path,
        busy_timeout_ms=100,
        max_busy_retries=0,
        busy_retry_sleep_seconds=0.05,
    )
    repository.initialize()
    reader = sqlite3.connect(db_path, check_same_thread=False)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM evidence_windows").fetchall()
    timer = threading.Timer(2.0, lambda: (reader.rollback(), reader.close()))
    timer.start()
    started = time.monotonic()
    try:
        diagnostic = repository.save_window_once_for_startup(
            window_id="startup-window",
            payload={"app_mode": "PAPER"},
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            deadline_seconds=5.0,
        )
    finally:
        timer.join()

    assert time.monotonic() - started >= 1.5
    assert diagnostic.attempt_count > 1
    assert diagnostic.operation == "shadow_evidence_window"
    assert repository.window_exists("startup-window")
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM evidence_windows WHERE window_id='startup-window'"
        ).fetchone()
    assert row == (1,)


def test_shadow_startup_waits_out_writer_contention(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(
        db_path,
        busy_timeout_ms=50,
        max_busy_retries=0,
        busy_retry_sleep_seconds=0.05,
    )
    repository.initialize()
    writer = sqlite3.connect(db_path, check_same_thread=False)
    writer.execute("BEGIN EXCLUSIVE")
    timer = threading.Timer(0.4, lambda: (writer.rollback(), writer.close()))
    timer.start()
    try:
        diagnostic = repository.save_window_once_for_startup(
            window_id="writer-window",
            payload={"app_mode": "PAPER"},
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            deadline_seconds=3.0,
        )
    finally:
        timer.join()

    assert diagnostic.attempt_count > 1
    assert repository.window_exists("writer-window")


def test_shadow_startup_busy_budget_exhaustion_has_no_fake_ready_window(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(
        db_path,
        busy_timeout_ms=50,
        max_busy_retries=0,
        busy_retry_sleep_seconds=0.05,
    )
    repository.initialize()
    reader = sqlite3.connect(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM evidence_windows").fetchall()
    try:
        try:
            repository.save_window_once_for_startup(
                window_id="blocked-window",
                payload={"app_mode": "PAPER"},
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                deadline_seconds=0.2,
            )
        except ShadowStorageBusy as exc:
            assert "STORAGE_BUSY:shadow_evidence_startup" in str(exc)
        else:
            raise AssertionError("expected startup storage busy failure")
    finally:
        reader.rollback()
        reader.close()

    assert not repository.window_exists("blocked-window")


def test_shadow_startup_retry_failure_closes_connections_for_later_success(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(
        db_path,
        busy_timeout_ms=50,
        max_busy_retries=0,
        busy_retry_sleep_seconds=0.05,
    )
    repository.initialize()
    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        try:
            repository.save_window_once_for_startup(
                window_id="retry-window",
                payload={"app_mode": "PAPER"},
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                deadline_seconds=0.2,
            )
        except ShadowStorageBusy:
            pass
        else:
            raise AssertionError("expected startup storage busy failure")
    finally:
        writer.rollback()
        writer.close()

    repository.save_window_once_for_startup(
        window_id="retry-window",
        payload={"app_mode": "PAPER"},
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        deadline_seconds=1.0,
    )

    assert repository.window_exists("retry-window")


def test_runtime_append_event_keeps_short_busy_policy(tmp_path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    repository = SQLiteShadowRepository(
        db_path,
        busy_timeout_ms=50,
        max_busy_retries=0,
        busy_retry_sleep_seconds=0.05,
    )
    repository.initialize()
    reader = sqlite3.connect(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM shadow_events").fetchall()
    started = time.monotonic()
    try:
        try:
            repository.append_event(
                event_id="event",
                window_id="window",
                event_type="REAL_SHADOW_CYCLE",
                bucket_key=None,
                payload={"app_mode": "PAPER"},
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        except ShadowStorageBusy:
            pass
        else:
            raise AssertionError("expected short runtime storage busy failure")
    finally:
        reader.rollback()
        reader.close()

    assert time.monotonic() - started < 1.0


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
