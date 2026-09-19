"""SQLite persistence for append-only V3.15 shadow evidence."""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from direction_engine_v3.domain._validation import require_text, require_utc


class ShadowStorageUnavailable(RuntimeError):
    """Raised when shadow evidence storage cannot be opened for the requested mode."""


class ShadowStorageBusy(RuntimeError):
    """Raised when bounded SQLite lock contention retry is exhausted."""


@dataclass(frozen=True)
class ShadowStartupStorageDiagnostic:
    """Safe startup storage diagnostic suitable for journals and deploy logs."""

    db_path: str
    sqlite_version: str
    journal_mode: str
    operation: str
    attempt_count: int
    elapsed_seconds: float
    last_sqlite_error_code: int | None = None
    last_sqlite_error_name: str | None = None
    last_error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "sqlite_version": self.sqlite_version,
            "journal_mode": self.journal_mode,
            "operation": self.operation,
            "attempt_count": self.attempt_count,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "last_sqlite_error_code": self.last_sqlite_error_code,
            "last_sqlite_error_name": self.last_sqlite_error_name,
            "last_error_message": self.last_error_message,
        }


class SQLiteShadowRepository:
    """Append-only evidence repository; stores no secrets or credentials."""

    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        busy_timeout_ms: int = 250,
        max_busy_retries: int = 3,
        busy_retry_sleep_seconds: float = 0.05,
    ) -> None:
        self._path = path
        self._read_only = read_only
        self._busy_timeout_ms = busy_timeout_ms
        self._max_busy_retries = max_busy_retries
        self._busy_retry_sleep_seconds = busy_retry_sleep_seconds

    def initialize(self) -> None:
        if self._read_only:
            raise ShadowStorageUnavailable("read-only shadow repository cannot initialize schema")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect(write=True)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_windows (
                    window_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_events (
                    event_id TEXT PRIMARY KEY,
                    window_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    bucket_key TEXT,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def initialize_for_startup(
        self, *, deadline_seconds: float = 30.0
    ) -> ShadowStartupStorageDiagnostic:
        """Initialize schema with a startup-only contention budget."""

        if self._read_only:
            raise ShadowStorageUnavailable("read-only shadow repository cannot initialize schema")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self._path.parent.mkdir(parents=True, exist_ok=True)

        return self._write_with_startup_retry(
            operation_name="shadow_evidence_schema",
            deadline_seconds=deadline_seconds,
            operation=lambda connection: connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_windows (
                    window_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_events (
                    event_id TEXT PRIMARY KEY,
                    window_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    bucket_key TEXT,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                """
            ),
        )

    def save_window_once(
        self, *, window_id: str, payload: Mapping[str, object], started_at: datetime
    ) -> None:
        require_text("window_id", window_id)
        require_utc("started_at", started_at)
        encoded = _encode(payload)
        self._write_with_retry(
            lambda connection: connection.execute(
                "INSERT OR IGNORE INTO evidence_windows VALUES (?,?,?)",
                (window_id, encoded, started_at.isoformat()),
            )
        )

    def save_window_once_for_startup(
        self,
        *,
        window_id: str,
        payload: Mapping[str, object],
        started_at: datetime,
        deadline_seconds: float = 30.0,
    ) -> ShadowStartupStorageDiagnostic:
        """Persist the startup evidence window under a bounded startup wait budget."""

        require_text("window_id", window_id)
        require_utc("started_at", started_at)
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        encoded = _encode(payload)

        diagnostic = self._write_with_startup_retry(
            operation_name="shadow_evidence_window",
            deadline_seconds=deadline_seconds,
            operation=lambda connection: connection.execute(
                "INSERT OR IGNORE INTO evidence_windows VALUES (?,?,?)",
                (window_id, encoded, started_at.isoformat()),
            ),
        )
        if not self.window_exists(window_id):
            raise ShadowStorageBusy("STORAGE_BUSY:shadow_evidence_startup_unverified")
        return diagnostic

    def append_event(
        self,
        *,
        event_id: str,
        window_id: str,
        event_type: str,
        bucket_key: str | None,
        payload: Mapping[str, object],
        observed_at: datetime,
    ) -> None:
        for name, value in (
            ("event_id", event_id),
            ("window_id", window_id),
            ("event_type", event_type),
        ):
            require_text(name, value)
        if bucket_key is not None:
            require_text("bucket_key", bucket_key)
        require_utc("observed_at", observed_at)
        self._write_with_retry(
            lambda connection: connection.execute(
                "INSERT OR IGNORE INTO shadow_events VALUES (?,?,?,?,?,?)",
                (
                    event_id,
                    window_id,
                    event_type,
                    bucket_key,
                    _encode(payload),
                    observed_at.isoformat(),
                ),
            )
        )

    def latest_window_payload(self) -> dict[str, object] | None:
        row = self._read_one(
            "SELECT payload_json FROM evidence_windows ORDER BY started_at DESC LIMIT 1"
        )
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise RuntimeError("stored evidence window payload is not an object")
        return payload

    def window_exists(self, window_id: str) -> bool:
        require_text("window_id", window_id)
        row = self._read_one(
            "SELECT 1 FROM evidence_windows WHERE window_id=? LIMIT 1", (window_id,)
        )
        return row is not None

    def event_counts(self) -> dict[str, int]:
        rows = self._read_all(
            "SELECT event_type, COUNT(*) FROM shadow_events GROUP BY event_type"
        )
        return {str(row[0]): int(str(row[1])) for row in rows}

    def journal_mode(self) -> str:
        row = self._read_one("PRAGMA journal_mode")
        return "" if row is None else str(row[0])

    def latest_events(
        self,
        *,
        event_type: str | None = None,
        bucket_key: str | None = None,
        limit: int = 250,
    ) -> tuple[dict[str, object], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            require_text("event_type", event_type)
            clauses.append("event_type=?")
            params.append(event_type)
        if bucket_key is not None:
            require_text("bucket_key", bucket_key)
            clauses.append("bucket_key=?")
            params.append(bucket_key)
        query = (
            "SELECT event_id,event_type,bucket_key,payload_json,observed_at "
            "FROM shadow_events"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)
        rows = self._read_all(query, tuple(params))
        events: list[dict[str, object]] = []
        for row in rows:
            payload = json.loads(str(row[3]))
            if not isinstance(payload, dict):
                raise RuntimeError("stored shadow event payload is not an object")
            events.append(
                {
                    "event_id": str(row[0]),
                    "event_type": str(row[1]),
                    "bucket_key": str(row[2]) if row[2] is not None else None,
                    "payload": payload,
                    "observed_at": str(row[4]),
                }
            )
        return tuple(events)

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        if write and self._read_only:
            raise ShadowStorageUnavailable("read-only shadow repository cannot write")
        if self._read_only:
            if not self._path.exists():
                raise ShadowStorageUnavailable("DATABASE_NOT_INITIALIZED")
            uri_path = self._path.resolve().as_posix().replace("?", "%3f").replace("#", "%23")
            connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro",
                timeout=self._busy_timeout_ms / 1000,
                uri=True,
            )
        else:
            connection = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1000,
            )
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return connection

    def _write_with_retry(self, operation: Callable[[sqlite3.Connection], object]) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(self._max_busy_retries + 1):
            try:
                with closing(self._connect(write=True)) as connection:
                    try:
                        operation(connection)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                return
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_busy(exc):
                    raise
                last_error = exc
                if attempt >= self._max_busy_retries:
                    break
                time.sleep(self._busy_retry_sleep_seconds * (attempt + 1))
        raise ShadowStorageBusy("STORAGE_BUSY:shadow_evidence_write") from last_error

    def _write_with_startup_retry(
        self,
        *,
        operation_name: str,
        deadline_seconds: float,
        operation: Callable[[sqlite3.Connection], object],
    ) -> ShadowStartupStorageDiagnostic:
        started = time.monotonic()
        deadline = started + deadline_seconds
        attempt_count = 0
        last_error: sqlite3.OperationalError | None = None
        while True:
            attempt_count += 1
            try:
                with closing(self._connect(write=True)) as connection:
                    try:
                        operation(connection)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                return self._startup_diagnostic(
                    operation=operation_name,
                    attempt_count=attempt_count,
                    elapsed_seconds=time.monotonic() - started,
                    last_error=last_error,
                )
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_busy(exc):
                    raise
                last_error = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(self._busy_retry_sleep_seconds * attempt_count, 0.5, remaining))
        diagnostic = self._startup_diagnostic(
            operation=operation_name,
            attempt_count=attempt_count,
            elapsed_seconds=time.monotonic() - started,
            last_error=last_error,
        )
        encoded_diagnostic = json.dumps(diagnostic.as_dict(), sort_keys=True)
        raise ShadowStorageBusy(
            f"STORAGE_BUSY:shadow_evidence_startup:{encoded_diagnostic}"
        ) from last_error

    def _startup_diagnostic(
        self,
        *,
        operation: str,
        attempt_count: int,
        elapsed_seconds: float,
        last_error: sqlite3.OperationalError | None,
    ) -> ShadowStartupStorageDiagnostic:
        error_message = None if last_error is None else _safe_sqlite_error_message(last_error)
        return ShadowStartupStorageDiagnostic(
            db_path=str(self._path.resolve()),
            sqlite_version=sqlite3.sqlite_version,
            journal_mode=self._safe_journal_mode(),
            operation=operation,
            attempt_count=attempt_count,
            elapsed_seconds=elapsed_seconds,
            last_sqlite_error_code=(
                None if last_error is None else getattr(last_error, "sqlite_errorcode", None)
            ),
            last_sqlite_error_name=(
                None if last_error is None else getattr(last_error, "sqlite_errorname", None)
            ),
            last_error_message=error_message,
        )

    def _safe_journal_mode(self) -> str:
        try:
            return self.journal_mode()
        except Exception:
            return "UNAVAILABLE"

    def _read_all(
        self, query: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [tuple(row) for row in rows]

    def _read_one(
        self, query: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else tuple(row)


def _is_sqlite_busy(exc: sqlite3.OperationalError) -> bool:
    error_name = str(getattr(exc, "sqlite_errorname", "")).upper()
    if error_name in {"SQLITE_BUSY", "SQLITE_LOCKED", "SQLITE_BUSY_SNAPSHOT"}:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _safe_sqlite_error_message(exc: sqlite3.OperationalError) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ").strip()[:240]


def _encode(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
