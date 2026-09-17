"""Immutable Directional training-ready observation storage."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_text, require_utc


@dataclass(frozen=True, slots=True)
class DirectionalCorpusRecord:
    record_id: str
    asset: Asset
    horizon: Horizon
    condition_id: str
    observed_at: datetime
    payload: Mapping[str, object]
    outcome_attached: bool


@dataclass(frozen=True, slots=True)
class DirectionalTrainingRecord:
    record_id: str
    asset: Asset
    horizon: Horizon
    condition_id: str
    observed_at: datetime
    payload: Mapping[str, object]
    outcome_up: bool
    outcome: Mapping[str, object]


class SQLiteDirectionalCorpusRepository:
    """Stores what was known before settlement, then immutable outcome evidence."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS directional_corpus (
                    record_id TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    outcome_json TEXT,
                    outcome_attached_at TEXT
                )
                """
            )

    def save_pre_outcome(
        self,
        *,
        record_id: str,
        asset: Asset,
        horizon: Horizon,
        condition_id: str,
        observed_at: datetime,
        payload: Mapping[str, object],
    ) -> DirectionalCorpusRecord:
        require_text("record_id", record_id)
        require_text("condition_id", condition_id)
        require_utc("observed_at", observed_at)
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO directional_corpus
                VALUES (?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    record_id,
                    asset.value,
                    horizon.value,
                    condition_id,
                    observed_at.isoformat(),
                    encoded,
                ),
            )
        stored = self.get(record_id)
        if stored is None:
            raise RuntimeError("directional corpus record was not persisted")
        return stored

    def attach_verified_outcome_once(
        self,
        *,
        record_id: str,
        outcome: Mapping[str, object],
        attached_at: datetime,
    ) -> None:
        require_text("record_id", record_id)
        require_utc("attached_at", attached_at)
        encoded = json.dumps(_jsonable(dict(outcome)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                UPDATE directional_corpus
                SET outcome_json=COALESCE(outcome_json, ?),
                    outcome_attached_at=COALESCE(outcome_attached_at, ?)
                WHERE record_id=?
                """,
                (encoded, attached_at.isoformat(), record_id),
            )

    def count(self, *, asset: Asset | None = None, horizon: Horizon | None = None) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if asset is not None:
            clauses.append("asset=?")
            params.append(asset.value)
        if horizon is not None:
            clauses.append("horizon=?")
            params.append(horizon.value)
        query = "SELECT COUNT(*) FROM directional_corpus"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return int(row[0])

    def training_ready_records(
        self, *, asset: Asset, horizon: Horizon
    ) -> tuple[DirectionalTrainingRecord, ...]:
        """Return official-outcome labeled records with complete feature vectors."""

        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT record_id,condition_id,observed_at,payload_json,outcome_json
                FROM directional_corpus
                WHERE asset=? AND horizon=? AND outcome_json IS NOT NULL
                ORDER BY observed_at ASC
                """,
                (asset.value, horizon.value),
            ).fetchall()
        records: list[DirectionalTrainingRecord] = []
        for row in rows:
            payload = json.loads(str(row[3]))
            outcome = json.loads(str(row[4]))
            if not isinstance(payload, dict) or not isinstance(outcome, dict):
                continue
            if not _is_training_ready_payload(payload):
                continue
            if outcome.get("settlement_source_kind") != "OFFICIAL":
                continue
            if not isinstance(outcome.get("outcome_up"), bool):
                continue
            records.append(
                DirectionalTrainingRecord(
                    record_id=str(row[0]),
                    asset=asset,
                    horizon=horizon,
                    condition_id=str(row[1]),
                    observed_at=datetime.fromisoformat(str(row[2])),
                    payload=payload,
                    outcome_up=bool(outcome["outcome_up"]),
                    outcome=outcome,
                )
            )
        return tuple(records)

    def get(self, record_id: str) -> DirectionalCorpusRecord | None:
        require_text("record_id", record_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT asset,horizon,condition_id,observed_at,payload_json,outcome_json
                FROM directional_corpus WHERE record_id=?
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return DirectionalCorpusRecord(
            record_id=record_id,
            asset=Asset(str(row[0])),
            horizon=Horizon(str(row[1])),
            condition_id=str(row[2]),
            observed_at=datetime.fromisoformat(str(row[3])),
            payload=json.loads(str(row[4])),
            outcome_attached=row[5] is not None,
        )

    @property
    def path(self) -> Path:
        return self._path


def _jsonable(value: object) -> object:
    from decimal import Decimal
    from enum import Enum

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


def _is_training_ready_payload(payload: Mapping[str, object]) -> bool:
    feature_vector = payload.get("feature_vector")
    if not isinstance(feature_vector, dict):
        return False
    feature_set_version = feature_vector.get("feature_set_version")
    features = feature_vector.get("features")
    if not isinstance(feature_set_version, str) or not feature_set_version:
        return False
    if not isinstance(features, list) or not features:
        return False
    names: set[str] = set()
    for item in features:
        if not isinstance(item, dict):
            return False
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            return False
        lowered = name.lower()
        if any(term in lowered for term in ("polymarket", "clob", "contract_price")):
            return False
        if "value" not in item or "source_ts" not in item:
            return False
        names.add(name)
    ptb = payload.get("price_to_beat")
    return bool(
        isinstance(ptb, dict) and ptb.get("persistence_id") and ptb.get("value")
    )
