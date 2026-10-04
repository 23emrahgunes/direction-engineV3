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


@dataclass(frozen=True, slots=True)
class DirectionalCheckpointRecord:
    checkpoint_id: str
    asset: Asset
    horizon: Horizon
    condition_id: str
    checkpoint_target_tte_seconds: int
    feature_schema_version: str
    observed_at: datetime
    actual_tte_seconds: int | None
    payload: Mapping[str, object]
    outcome_attached: bool


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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_corpus_condition_observed
                ON directional_corpus(condition_id, observed_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_corpus_bucket_observed
                ON directional_corpus(asset, horizon, observed_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_corpus_training_ready
                ON directional_corpus(asset, horizon, observed_at)
                WHERE outcome_json IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS directional_checkpoint_observations (
                    checkpoint_id TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    checkpoint_target_tte_seconds INTEGER NOT NULL,
                    feature_schema_version TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actual_tte_seconds INTEGER,
                    payload_json TEXT NOT NULL,
                    outcome_json TEXT,
                    outcome_attached_at TEXT,
                    UNIQUE(
                        condition_id,
                        asset,
                        horizon,
                        checkpoint_target_tte_seconds,
                        feature_schema_version
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_checkpoint_bucket_target
                ON directional_checkpoint_observations(
                    asset,
                    horizon,
                    checkpoint_target_tte_seconds,
                    observed_at
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_checkpoint_condition
                ON directional_checkpoint_observations(condition_id, observed_at)
                """
            )

    def save_checkpoint_observation(
        self,
        *,
        checkpoint_id: str,
        asset: Asset,
        horizon: Horizon,
        condition_id: str,
        checkpoint_target_tte_seconds: int,
        feature_schema_version: str,
        observed_at: datetime,
        actual_tte_seconds: int | None,
        payload: Mapping[str, object],
    ) -> DirectionalCheckpointRecord:
        require_text("checkpoint_id", checkpoint_id)
        require_text("condition_id", condition_id)
        require_text("feature_schema_version", feature_schema_version)
        require_utc("observed_at", observed_at)
        if (
            isinstance(checkpoint_target_tte_seconds, bool)
            or checkpoint_target_tte_seconds <= 0
        ):
            raise ValueError("checkpoint_target_tte_seconds must be positive")
        if actual_tte_seconds is not None and isinstance(actual_tte_seconds, bool):
            raise TypeError("actual_tte_seconds must be an integer or None")
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO directional_checkpoint_observations
                VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    checkpoint_id,
                    asset.value,
                    horizon.value,
                    condition_id,
                    checkpoint_target_tte_seconds,
                    feature_schema_version,
                    observed_at.isoformat(),
                    actual_tte_seconds,
                    encoded,
                ),
            )
        stored = self.get_checkpoint(checkpoint_id)
        if stored is None:
            stored = self.get_checkpoint_by_key(
                condition_id=condition_id,
                asset=asset,
                horizon=horizon,
                checkpoint_target_tte_seconds=checkpoint_target_tte_seconds,
                feature_schema_version=feature_schema_version,
            )
        if stored is None:
            raise RuntimeError("directional checkpoint record was not persisted")
        return stored

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
            row = connection.execute(
                "SELECT outcome_json FROM directional_corpus WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            if row[0] is not None:
                if str(row[0]) != encoded:
                    raise RuntimeError("conflicting official outcome for corpus record")
                return
            connection.execute(
                """
                UPDATE directional_corpus
                SET outcome_json=?,
                    outcome_attached_at=?
                WHERE record_id=?
                """,
                (encoded, attached_at.isoformat(), record_id),
            )

    def attach_verified_outcome_to_condition_once(
        self,
        *,
        condition_id: str,
        outcome: Mapping[str, object],
        official_resolved_at: datetime,
        attached_at: datetime,
    ) -> int:
        require_text("condition_id", condition_id)
        require_utc("official_resolved_at", official_resolved_at)
        require_utc("attached_at", attached_at)
        if outcome.get("settlement_source_kind") != "OFFICIAL":
            raise ValueError("corpus outcomes must be official")
        encoded = json.dumps(_jsonable(dict(outcome)), sort_keys=True, separators=(",", ":"))
        updated = 0
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT record_id,observed_at,outcome_json FROM directional_corpus
                WHERE condition_id=?
                """,
                (condition_id,),
            ).fetchall()
            for record_id, observed_at_raw, existing in rows:
                observed_at = datetime.fromisoformat(str(observed_at_raw))
                if observed_at >= official_resolved_at:
                    continue
                if existing is not None:
                    if str(existing) != encoded:
                        raise RuntimeError("conflicting official outcome for condition")
                    continue
                connection.execute(
                    """
                    UPDATE directional_corpus
                    SET outcome_json=?, outcome_attached_at=?
                    WHERE record_id=?
                    """,
                    (encoded, attached_at.isoformat(), str(record_id)),
                )
                updated += 1
            checkpoint_rows = connection.execute(
                """
                SELECT checkpoint_id,observed_at,outcome_json
                FROM directional_checkpoint_observations
                WHERE condition_id=?
                """,
                (condition_id,),
            ).fetchall()
            for checkpoint_id, observed_at_raw, existing in checkpoint_rows:
                observed_at = datetime.fromisoformat(str(observed_at_raw))
                if observed_at >= official_resolved_at:
                    continue
                if existing is not None:
                    if str(existing) != encoded:
                        raise RuntimeError("conflicting official outcome for checkpoint")
                    continue
                connection.execute(
                    """
                    UPDATE directional_checkpoint_observations
                    SET outcome_json=?, outcome_attached_at=?
                    WHERE checkpoint_id=?
                    """,
                    (encoded, attached_at.isoformat(), str(checkpoint_id)),
                )
                updated += 1
        return updated

    def get_checkpoint(self, checkpoint_id: str) -> DirectionalCheckpointRecord | None:
        require_text("checkpoint_id", checkpoint_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT asset,horizon,condition_id,checkpoint_target_tte_seconds,
                       feature_schema_version,observed_at,actual_tte_seconds,
                       payload_json,outcome_json
                FROM directional_checkpoint_observations
                WHERE checkpoint_id=?
                """,
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return _checkpoint_from_row(checkpoint_id, row)

    def get_checkpoint_by_key(
        self,
        *,
        condition_id: str,
        asset: Asset,
        horizon: Horizon,
        checkpoint_target_tte_seconds: int,
        feature_schema_version: str,
    ) -> DirectionalCheckpointRecord | None:
        require_text("condition_id", condition_id)
        require_text("feature_schema_version", feature_schema_version)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT checkpoint_id,asset,horizon,condition_id,
                       checkpoint_target_tte_seconds,feature_schema_version,
                       observed_at,actual_tte_seconds,payload_json,outcome_json
                FROM directional_checkpoint_observations
                WHERE condition_id=? AND asset=? AND horizon=?
                  AND checkpoint_target_tte_seconds=? AND feature_schema_version=?
                """,
                (
                    condition_id,
                    asset.value,
                    horizon.value,
                    checkpoint_target_tte_seconds,
                    feature_schema_version,
                ),
            ).fetchone()
        if row is None:
            return None
        return _checkpoint_from_row(str(row[0]), row[1:])

    def checkpoint_quality_report(self) -> dict[str, object]:
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT asset,horizon,checkpoint_target_tte_seconds,
                       COUNT(*),
                       COUNT(DISTINCT condition_id),
                       SUM(CASE WHEN outcome_json IS NOT NULL THEN 1 ELSE 0 END),
                       COUNT(DISTINCT CASE WHEN outcome_json IS NOT NULL THEN condition_id END),
                       SUM(CASE WHEN actual_tte_seconds IS NULL THEN 1 ELSE 0 END)
                FROM directional_checkpoint_observations
                GROUP BY asset,horizon,checkpoint_target_tte_seconds
                ORDER BY asset,horizon,checkpoint_target_tte_seconds
                """
            ).fetchall()
            duplicate_rows = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT condition_id,asset,horizon,checkpoint_target_tte_seconds,
                           feature_schema_version,COUNT(*) AS row_count
                    FROM directional_checkpoint_observations
                    GROUP BY condition_id,asset,horizon,checkpoint_target_tte_seconds,
                             feature_schema_version
                    HAVING row_count > 1
                )
                """
            ).fetchone()
            feature_rows = connection.execute(
                "SELECT payload_json FROM directional_checkpoint_observations"
            ).fetchall()
            schema_rows = connection.execute(
                """
                SELECT feature_schema_version,COUNT(*)
                FROM directional_checkpoint_observations
                GROUP BY feature_schema_version
                ORDER BY feature_schema_version
                """
            ).fetchall()
        missing_counts: dict[str, int] = {}
        feature_counts: dict[str, int] = {}
        future_timestamp_violations = 0
        for (payload_raw,) in feature_rows:
            payload = json.loads(str(payload_raw))
            feature_vector = payload.get("feature_vector")
            if not isinstance(feature_vector, dict):
                missing_counts["feature_vector"] = missing_counts.get("feature_vector", 0) + 1
                continue
            generated_at = _optional_datetime(feature_vector.get("generated_at"))
            features = feature_vector.get("features")
            if not isinstance(features, list):
                missing_counts["features"] = missing_counts.get("features", 0) + 1
                continue
            for item in features:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "UNKNOWN")
                feature_counts[name] = feature_counts.get(name, 0) + 1
                if item.get("value") is None:
                    missing_counts[name] = missing_counts.get(name, 0) + 1
                source_ts = _optional_datetime(item.get("source_ts"))
                if generated_at is not None and source_ts is not None and source_ts > generated_at:
                    future_timestamp_violations += 1
        buckets = [
            {
                "asset": str(row[0]),
                "horizon": str(row[1]),
                "checkpoint_target_tte_seconds": int(row[2]),
                "row_count": int(row[3] or 0),
                "unique_condition_count": int(row[4] or 0),
                "labeled_row_count": int(row[5] or 0),
                "labeled_unique_condition_count": int(row[6] or 0),
                "missing_actual_tte_count": int(row[7] or 0),
            }
            for row in rows
        ]
        return {
            "status": "DIRECTIONAL_CHECKPOINT_DATASET_READY",
            "checkpoint_targets_seconds": [120, 90, 60, 45],
            "checkpoint_tolerance_seconds": 10,
            "buckets": buckets,
            "duplicate_reject_count": int((duplicate_rows or (0,))[0] or 0),
            "feature_schema_versions": {
                str(row[0]): int(row[1] or 0) for row in schema_rows
            },
            "missing_feature_counts": missing_counts,
            "feature_observation_counts": feature_counts,
            "future_timestamp_violations": future_timestamp_violations,
        }

    def records_for_condition(self, condition_id: str) -> tuple[DirectionalCorpusRecord, ...]:
        require_text("condition_id", condition_id)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT record_id,asset,horizon,condition_id,observed_at,payload_json,outcome_json
                FROM directional_corpus WHERE condition_id=? ORDER BY observed_at ASC
                """,
                (condition_id,),
            ).fetchall()
        return tuple(
            DirectionalCorpusRecord(
                record_id=str(row[0]),
                asset=Asset(str(row[1])),
                horizon=Horizon(str(row[2])),
                condition_id=str(row[3]),
                observed_at=datetime.fromisoformat(str(row[4])),
                payload=json.loads(str(row[5])),
                outcome_attached=row[6] is not None,
            )
            for row in rows
        )

    def corpus_counts(
        self, *, asset: Asset | None = None, horizon: Horizon | None = None
    ) -> dict[str, int]:
        clauses: list[str] = []
        params: list[object] = []
        if asset is not None:
            clauses.append("asset=?")
            params.append(asset.value)
        if horizon is not None:
            clauses.append("horizon=?")
            params.append(horizon.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT COUNT(*),"
                "SUM(CASE WHEN outcome_json IS NOT NULL THEN 1 ELSE 0 END),"
                "COUNT(DISTINCT condition_id),"
                "COUNT(DISTINCT CASE WHEN outcome_json IS NOT NULL THEN condition_id END) "
                f"FROM directional_corpus{where}",
                tuple(params),
            ).fetchone()
        return {
            "observation_count": int(row[0] or 0),
            "labeled_observation_count": int(row[1] or 0),
            "unique_condition_count": int(row[2] or 0),
            "labeled_unique_condition_count": int(row[3] or 0),
        }

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
        self, *, asset: Asset, horizon: Horizon, limit: int | None = None
    ) -> tuple[DirectionalTrainingRecord, ...]:
        """Return official-outcome labeled records with complete feature vectors."""

        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        query = """
            SELECT record_id,condition_id,observed_at,payload_json,outcome_json
            FROM directional_corpus
            WHERE asset=? AND horizon=? AND outcome_json IS NOT NULL
            ORDER BY observed_at ASC
            """
        params: tuple[object, ...] = (asset.value, horizon.value)
        if limit is not None:
            query += " LIMIT ?"
            params = (*params, limit)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, params).fetchall()
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


def _checkpoint_from_row(
    checkpoint_id: str, row: tuple[object, ...]
) -> DirectionalCheckpointRecord:
    return DirectionalCheckpointRecord(
        checkpoint_id=checkpoint_id,
        asset=Asset(str(row[0])),
        horizon=Horizon(str(row[1])),
        condition_id=str(row[2]),
        checkpoint_target_tte_seconds=int(str(row[3])),
        feature_schema_version=str(row[4]),
        observed_at=datetime.fromisoformat(str(row[5])),
        actual_tte_seconds=None if row[6] is None else int(str(row[6])),
        payload=json.loads(str(row[7])),
        outcome_attached=row[8] is not None,
    )


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
