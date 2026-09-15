"""SQLite persistence for append-only V3.15 shadow evidence."""

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from direction_engine_v3.domain._validation import require_text, require_utc


class SQLiteShadowRepository:
    """Append-only evidence repository; stores no secrets or credentials."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
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

    def save_window_once(
        self, *, window_id: str, payload: Mapping[str, object], started_at: datetime
    ) -> None:
        require_text("window_id", window_id)
        require_utc("started_at", started_at)
        encoded = _encode(payload)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO evidence_windows VALUES (?,?,?)",
                (window_id, encoded, started_at.isoformat()),
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
        with sqlite3.connect(self._path) as connection:
            connection.execute(
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

    def latest_window_payload(self) -> dict[str, object] | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM evidence_windows ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise RuntimeError("stored evidence window payload is not an object")
        return payload

    def event_counts(self) -> dict[str, int]:
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT event_type, COUNT(*) FROM shadow_events GROUP BY event_type"
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}


def _encode(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
