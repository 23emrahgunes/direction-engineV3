"""SQLite-backed append-only PAPER execution audit repository."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from direction_engine_v3.domain._validation import require_text, require_utc


@dataclass(frozen=True, slots=True)
class StoredExecution:
    idempotency_key: str
    plan_id: str
    payload: Mapping[str, object]
    recorded_at: datetime


class SQLitePaperRepository:
    """Durable idempotency and immutable audit events; never stores credentials."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_executions (
                    idempotency_key TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    reference_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    def get(self, idempotency_key: str) -> StoredExecution | None:
        require_text("idempotency_key", idempotency_key)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT plan_id,payload_json,recorded_at FROM paper_executions "
                "WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[1])
        if not isinstance(payload, dict):
            raise RuntimeError("stored execution payload is not an object")
        return StoredExecution(idempotency_key, row[0], payload, datetime.fromisoformat(row[2]))

    def save_once(
        self,
        *,
        idempotency_key: str,
        plan_id: str,
        payload: Mapping[str, object],
        recorded_at: datetime,
    ) -> StoredExecution:
        require_text("idempotency_key", idempotency_key)
        require_text("plan_id", plan_id)
        require_utc("recorded_at", recorded_at)
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO paper_executions VALUES (?,?,?,?)",
                (idempotency_key, plan_id, encoded, recorded_at.isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO audit_events VALUES (?,?,?,?,?)",
                (
                    f"paper:{idempotency_key}",
                    plan_id,
                    "PAPER_EXECUTION",
                    encoded,
                    recorded_at.isoformat(),
                ),
            )
        stored = self.get(idempotency_key)
        if stored is None:
            raise RuntimeError("execution was not durably recorded")
        return stored

    def audit_count(self, reference_id: str) -> int:
        require_text("reference_id", reference_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE reference_id=?", (reference_id,)
            ).fetchone()
        return int(row[0]) if row is not None else 0


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
