"""SQLite persistence for append-only V3.15 shadow evidence."""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime
from pathlib import Path

from direction_engine_v3.domain._validation import require_text, require_utc


class ShadowStorageUnavailable(RuntimeError):
    """Raised when shadow evidence storage cannot be opened for the requested mode."""


class ShadowStorageBusy(RuntimeError):
    """Raised when bounded SQLite lock contention retry is exhausted."""


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
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _encode(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
