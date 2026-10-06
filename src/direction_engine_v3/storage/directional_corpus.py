"""Immutable Directional training-ready observation storage."""

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.domain._validation import require_text, require_utc

READINESS_POLICY_VERSION = "DIRECTIONAL_CORPUS_READINESS_V2"
MINIMUM_LABELED_UNIQUE_CONDITIONS = 100
MINIMUM_CLASS_COUNT_PER_SIDE = 20
MINIMUM_ELIGIBLE_LABEL_COVERAGE = 0.80
CHECKPOINT_TARGETS_SECONDS = (120, 90, 60, 45)
CHECKPOINT_TOLERANCE_SECONDS = 10
SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION = "SOL5M_PROSPECTIVE_EVIDENCE_V2"
SOL5M_PROSPECTIVE_LEGACY_SCHEMA_VERSION = "SOL5M_PROSPECTIVE_EVIDENCE_V1"
SOL5M_PROSPECTIVE_MINIMUM_LABELED_CONDITIONS = 100
SOL5M_PROSPECTIVE_MINIMUM_PRICING_COVERAGE = 0.80

PLACEHOLDER_NON_SIGNAL_FEATURES = frozenset({"spot_perp_basis"})
REQUIRED_SIGNAL_FEATURES = frozenset(
    {
        "ptb_normalized_distance",
        "tte_fraction",
        "short_return",
        "medium_return",
        "momentum",
        "realized_volatility",
        "volatility_acceleration",
        "trade_imbalance",
        "external_book_imbalance",
        "microprice_distance",
        "signal_stability",
        "flip_rate",
        "regime_score",
    }
)
DERIVED_DIAGNOSTIC_FEATURES = frozenset({"signal_stability", "flip_rate"})


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


@dataclass(frozen=True, slots=True)
class DirectionalCheckpointLabelCandidate:
    condition_id: str
    asset: Asset
    horizon: Horizon
    market_id: str | None
    window_start: datetime | None
    window_end: datetime | None
    checkpoint_row_count: int
    unlabeled_row_count: int


@dataclass(frozen=True, slots=True)
class DirectionalUnlabeledCheckpointCondition:
    condition_id: str
    asset: Asset
    horizon: Horizon
    market_id: str | None
    window_start: datetime | None
    window_end: datetime | None
    first_observed_at: datetime
    last_observed_at: datetime
    checkpoint_row_count: int
    unlabeled_row_count: int


@dataclass(frozen=True, slots=True)
class Sol5mProspectiveEvidenceRecord:
    evidence_id: str
    condition_id: str
    observed_at: datetime
    payload: Mapping[str, object]
    outcome_attached: bool


class SQLiteDirectionalCorpusRepository:
    """Stores what was known before settlement, then immutable outcome evidence."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directional_checkpoint_unlabeled_condition
                ON directional_checkpoint_observations(condition_id, asset, horizon, observed_at)
                WHERE outcome_json IS NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sol5m_prospective_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    condition_id TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    checkpoint_target_tte_seconds INTEGER NOT NULL,
                    evidence_schema_version TEXT NOT NULL,
                    feature_schema_version TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_artifact_checksum TEXT,
                    observed_at TEXT NOT NULL,
                    actual_tte_seconds INTEGER,
                    predicted_side TEXT,
                    raw_model_probability REAL,
                    calibrated_probability REAL,
                    selected_probability REAL,
                    selected_side_executable_cost REAL,
                    up_executable_cost REAL,
                    down_executable_cost REAL,
                    pricing_status TEXT NOT NULL,
                    prediction_status TEXT,
                    prediction_failure_reason TEXT,
                    pricing_failure_reason TEXT,
                    label_status TEXT,
                    economic_eligible INTEGER,
                    valid_capture_start TEXT,
                    timing_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    outcome_json TEXT,
                    outcome_attached_at TEXT,
                    UNIQUE(
                        condition_id,
                        checkpoint_target_tte_seconds,
                        model_id,
                        evidence_schema_version
                    )
                )
                """
            )
            self._ensure_sol5m_prospective_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sol5m_prospective_observed
                ON sol5m_prospective_evidence(observed_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sol5m_prospective_unlabeled
                ON sol5m_prospective_evidence(condition_id, observed_at)
                WHERE outcome_json IS NULL
                """
            )

    @staticmethod
    def _ensure_sol5m_prospective_columns(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(sol5m_prospective_evidence)")
        }
        additions = {
            "prediction_status": "TEXT",
            "prediction_failure_reason": "TEXT",
            "pricing_failure_reason": "TEXT",
            "label_status": "TEXT",
            "economic_eligible": "INTEGER",
            "valid_capture_start": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE sol5m_prospective_evidence ADD COLUMN {name} {column_type}"
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
            self._ensure_sol5m_prospective_columns(connection)
            prospective_rows = connection.execute(
                """
                SELECT evidence_id,observed_at,outcome_json
                FROM sol5m_prospective_evidence
                WHERE condition_id=?
                """,
                (condition_id,),
            ).fetchall()
            for evidence_id, observed_at_raw, existing in prospective_rows:
                observed_at = datetime.fromisoformat(str(observed_at_raw))
                if observed_at >= official_resolved_at:
                    continue
                if existing is not None:
                    if str(existing) != encoded:
                        raise RuntimeError(
                            "conflicting official outcome for prospective evidence"
                        )
                    continue
                connection.execute(
                    """
                    UPDATE sol5m_prospective_evidence
                    SET outcome_json=?, outcome_attached_at=?, label_status='LABELED'
                    WHERE evidence_id=?
                    """,
                    (encoded, attached_at.isoformat(), str(evidence_id)),
                )
                updated += 1
        return updated

    def save_sol5m_prospective_evidence(
        self,
        *,
        evidence_id: str,
        condition_id: str,
        market_id: str,
        checkpoint_target_tte_seconds: int,
        evidence_schema_version: str,
        feature_schema_version: str,
        model_id: str,
        model_artifact_checksum: str | None,
        observed_at: datetime,
        actual_tte_seconds: int | None,
        predicted_side: str | None,
        raw_model_probability: float | None,
        calibrated_probability: float | None,
        selected_probability: float | None,
        selected_side_executable_cost: float | None,
        up_executable_cost: float | None,
        down_executable_cost: float | None,
        pricing_status: str,
        timing_status: str,
        prediction_status: str | None = None,
        prediction_failure_reason: str | None = None,
        pricing_failure_reason: str | None = None,
        label_status: str | None = None,
        economic_eligible: bool | None = None,
        valid_capture_start: datetime | None = None,
        payload: Mapping[str, object],
    ) -> Sol5mProspectiveEvidenceRecord:
        require_text("evidence_id", evidence_id)
        require_text("condition_id", condition_id)
        require_text("market_id", market_id)
        require_text("evidence_schema_version", evidence_schema_version)
        require_text("feature_schema_version", feature_schema_version)
        require_text("model_id", model_id)
        require_text("pricing_status", pricing_status)
        require_text("timing_status", timing_status)
        require_utc("observed_at", observed_at)
        if prediction_status is not None:
            require_text("prediction_status", prediction_status)
        if pricing_failure_reason is not None:
            require_text("pricing_failure_reason", pricing_failure_reason)
        if prediction_failure_reason is not None:
            require_text("prediction_failure_reason", prediction_failure_reason)
        if label_status is not None:
            require_text("label_status", label_status)
        if valid_capture_start is not None:
            require_utc("valid_capture_start", valid_capture_start)
        if checkpoint_target_tte_seconds != 45:
            raise ValueError("SOL-5m prospective evidence captures only the 45s checkpoint")
        if prediction_status is None:
            prediction_status = (
                "VALID"
                if predicted_side is not None and calibrated_probability is not None
                else "UNAVAILABLE"
            )
        if label_status is None:
            label_status = "UNLABELED"
        if economic_eligible is None:
            economic_eligible = (
                prediction_status == "VALID"
                and pricing_status == "CHECKPOINT_EXECUTABLE_PRICING_READY"
                and selected_side_executable_cost is not None
            )
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            self._ensure_sol5m_prospective_columns(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO sol5m_prospective_evidence (
                    evidence_id,
                    condition_id,
                    market_id,
                    asset,
                    horizon,
                    checkpoint_target_tte_seconds,
                    evidence_schema_version,
                    feature_schema_version,
                    model_id,
                    model_artifact_checksum,
                    observed_at,
                    actual_tte_seconds,
                    predicted_side,
                    raw_model_probability,
                    calibrated_probability,
                    selected_probability,
                    selected_side_executable_cost,
                    up_executable_cost,
                    down_executable_cost,
                    pricing_status,
                    prediction_status,
                    prediction_failure_reason,
                    pricing_failure_reason,
                    label_status,
                    economic_eligible,
                    valid_capture_start,
                    timing_status,
                    payload_json,
                    outcome_json,
                    outcome_attached_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    evidence_id,
                    condition_id,
                    market_id,
                    Asset.SOL.value,
                    Horizon.FIVE_MINUTES.value,
                    checkpoint_target_tte_seconds,
                    evidence_schema_version,
                    feature_schema_version,
                    model_id,
                    model_artifact_checksum,
                    observed_at.isoformat(),
                    actual_tte_seconds,
                    predicted_side,
                    raw_model_probability,
                    calibrated_probability,
                    selected_probability,
                    selected_side_executable_cost,
                    up_executable_cost,
                    down_executable_cost,
                    pricing_status,
                    prediction_status,
                    prediction_failure_reason,
                    pricing_failure_reason,
                    label_status,
                    None if economic_eligible is None else int(economic_eligible),
                    None if valid_capture_start is None else valid_capture_start.isoformat(),
                    timing_status,
                    encoded,
                ),
            )
        stored = self.get_sol5m_prospective_evidence(evidence_id)
        if stored is None:
            stored = self.get_sol5m_prospective_evidence_by_key(
                condition_id=condition_id,
                checkpoint_target_tte_seconds=checkpoint_target_tte_seconds,
                model_id=model_id,
                evidence_schema_version=evidence_schema_version,
            )
        if stored is None:
            raise RuntimeError("SOL-5m prospective evidence was not persisted")
        return stored

    def get_sol5m_prospective_evidence(
        self, evidence_id: str
    ) -> Sol5mProspectiveEvidenceRecord | None:
        require_text("evidence_id", evidence_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT condition_id,observed_at,payload_json,outcome_json
                FROM sol5m_prospective_evidence WHERE evidence_id=?
                """,
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return Sol5mProspectiveEvidenceRecord(
            evidence_id=evidence_id,
            condition_id=str(row[0]),
            observed_at=datetime.fromisoformat(str(row[1])),
            payload=json.loads(str(row[2])),
            outcome_attached=row[3] is not None,
        )

    def get_sol5m_prospective_evidence_by_key(
        self,
        *,
        condition_id: str,
        checkpoint_target_tte_seconds: int,
        model_id: str,
        evidence_schema_version: str,
    ) -> Sol5mProspectiveEvidenceRecord | None:
        require_text("condition_id", condition_id)
        require_text("model_id", model_id)
        require_text("evidence_schema_version", evidence_schema_version)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT evidence_id,condition_id,observed_at,payload_json,outcome_json
                FROM sol5m_prospective_evidence
                WHERE condition_id=? AND checkpoint_target_tte_seconds=?
                  AND model_id=? AND evidence_schema_version=?
                """,
                (
                    condition_id,
                    checkpoint_target_tte_seconds,
                    model_id,
                    evidence_schema_version,
                ),
            ).fetchone()
        if row is None:
            return None
        return Sol5mProspectiveEvidenceRecord(
            evidence_id=str(row[0]),
            condition_id=str(row[1]),
            observed_at=datetime.fromisoformat(str(row[2])),
            payload=json.loads(str(row[3])),
            outcome_attached=row[4] is not None,
        )

    def sol5m_prospective_evidence_summary(self) -> dict[str, object]:
        with sqlite3.connect(self._path) as connection:
            try:
                row = connection.execute(
                    """
                    SELECT COUNT(*),
                           COUNT(DISTINCT condition_id),
                           SUM(CASE WHEN outcome_json IS NOT NULL THEN 1 ELSE 0 END),
                           COUNT(DISTINCT CASE WHEN outcome_json IS NOT NULL THEN condition_id END),
                           SUM(
                               CASE WHEN selected_side_executable_cost IS NOT NULL
                               THEN 1 ELSE 0 END
                           ),
                           MIN(observed_at),
                           MAX(observed_at)
                    FROM sol5m_prospective_evidence
                    """
                ).fetchone()
                label_rows = connection.execute(
                    """
                    SELECT outcome_json,COUNT(DISTINCT condition_id)
                    FROM sol5m_prospective_evidence
                    WHERE outcome_json IS NOT NULL
                    GROUP BY outcome_json
                    """
                ).fetchall()
                status_rows = connection.execute(
                    """
                    SELECT pricing_status,COUNT(*)
                    FROM sol5m_prospective_evidence
                    GROUP BY pricing_status
                    ORDER BY pricing_status
                    """
                ).fetchall()
                prediction_rows = connection.execute(
                    """
                    SELECT COALESCE(prediction_status,'LEGACY_NOT_REPORTED'),COUNT(*)
                    FROM sol5m_prospective_evidence
                    GROUP BY COALESCE(prediction_status,'LEGACY_NOT_REPORTED')
                    ORDER BY COALESCE(prediction_status,'LEGACY_NOT_REPORTED')
                    """
                ).fetchall()
                prediction_reason_rows = connection.execute(
                    """
                    SELECT COALESCE(prediction_failure_reason,'NONE'),COUNT(*)
                    FROM sol5m_prospective_evidence
                    WHERE prediction_status IS NOT NULL
                      AND prediction_status <> 'VALID'
                    GROUP BY COALESCE(prediction_failure_reason,'NONE')
                    ORDER BY COALESCE(prediction_failure_reason,'NONE')
                    """
                ).fetchall()
                pricing_reason_rows = connection.execute(
                    """
                    SELECT COALESCE(pricing_failure_reason,'NONE'),COUNT(*)
                    FROM sol5m_prospective_evidence
                    WHERE pricing_failure_reason IS NOT NULL
                    GROUP BY COALESCE(pricing_failure_reason,'NONE')
                    ORDER BY COALESCE(pricing_failure_reason,'NONE')
                    """
                ).fetchall()
                v2_row = connection.execute(
                    """
                    SELECT COUNT(*),
                           COUNT(DISTINCT condition_id),
                           SUM(CASE WHEN prediction_status='VALID' THEN 1 ELSE 0 END),
                           SUM(
                               CASE WHEN pricing_status='CHECKPOINT_EXECUTABLE_PRICING_READY'
                               THEN 1 ELSE 0 END
                           ),
                           SUM(CASE WHEN economic_eligible=1 THEN 1 ELSE 0 END),
                           MIN(observed_at)
                    FROM sol5m_prospective_evidence
                    WHERE evidence_schema_version=?
                    """,
                    (SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,),
                ).fetchone()
                legacy_unavailable = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sol5m_prospective_evidence
                    WHERE evidence_schema_version=?
                      AND pricing_status='PREDICTION_UNAVAILABLE'
                    """,
                    (SOL5M_PROSPECTIVE_LEGACY_SCHEMA_VERSION,),
                ).fetchone()
                blocker_rows = connection.execute(
                    """
                    SELECT timing_status,COUNT(*)
                    FROM sol5m_prospective_evidence
                    GROUP BY timing_status
                    ORDER BY timing_status
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                return {
                    "status": "SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_UNAVAILABLE",
                    "schema_version": SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
                    "real_order_submission": False,
                    "marker": "SOL5M_PROSPECTIVE_EVIDENCE_BLOCKED",
                    "reason": "TABLE_UNAVAILABLE",
                }
        total_rows = int((row or (0,))[0] or 0)
        unique_conditions = int((row or (0, 0))[1] or 0)
        labeled_rows = int((row or (0, 0, 0))[2] or 0)
        labeled_unique = int((row or (0, 0, 0, 0))[3] or 0)
        priced_rows = int((row or (0, 0, 0, 0, 0))[4] or 0)
        pricing_coverage = priced_rows / total_rows if total_rows else 0.0
        v2_total = int((v2_row or (0,))[0] or 0)
        v2_unique = int((v2_row or (0, 0))[1] or 0)
        prediction_valid = int((v2_row or (0, 0, 0))[2] or 0)
        pricing_valid = int((v2_row or (0, 0, 0, 0))[3] or 0)
        economic_eligible = int((v2_row or (0, 0, 0, 0, 0))[4] or 0)
        v2_pricing_coverage = pricing_valid / v2_total if v2_total else 0.0
        label_counts = {"UP": 0, "DOWN": 0}
        for outcome_raw, count in label_rows:
            label = _label_from_outcome(_safe_json_object(str(outcome_raw or "{}")))
            if label in label_counts:
                label_counts[label] += int(count or 0)
        marker = (
            "SOL5M_PROSPECTIVE_EVIDENCE_CAPTURE_ACCEPTED"
            if total_rows > 0
            else "SOL5M_PROSPECTIVE_EVIDENCE_COLLECTING"
        )
        if labeled_unique < SOL5M_PROSPECTIVE_MINIMUM_LABELED_CONDITIONS:
            evaluation_state = "COLLECTING"
        elif pricing_coverage < SOL5M_PROSPECTIVE_MINIMUM_PRICING_COVERAGE:
            evaluation_state = "INSUFFICIENT_PRICING_COVERAGE"
        else:
            evaluation_state = "READY_FOR_PROSPECTIVE_EVALUATION"
        return {
            "status": "SOL5M_PROSPECTIVE_EVIDENCE_READY",
            "schema_version": SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
            "asset": Asset.SOL.value,
            "horizon": Horizon.FIVE_MINUTES.value,
            "checkpoint_target_tte_seconds": 45,
            "minimum_labeled_unique_conditions": (
                SOL5M_PROSPECTIVE_MINIMUM_LABELED_CONDITIONS
            ),
            "minimum_pricing_coverage": str(SOL5M_PROSPECTIVE_MINIMUM_PRICING_COVERAGE),
            "total_rows": total_rows,
            "unique_conditions": unique_conditions,
            "labeled_rows": labeled_rows,
            "labeled_unique_conditions": labeled_unique,
            "up_labeled_conditions": label_counts["UP"],
            "down_labeled_conditions": label_counts["DOWN"],
            "priced_rows": priced_rows,
            "pricing_coverage": str(pricing_coverage),
            "first_observed_at": None if row is None else row[5],
            "latest_observed_at": None if row is None else row[6],
            "pricing_status_counts": {
                str(status): int(count or 0) for status, count in status_rows
            },
            "prediction_status_counts": {
                str(status): int(count or 0) for status, count in prediction_rows
            },
            "prediction_failure_reason_counts": {
                str(reason): int(count or 0) for reason, count in prediction_reason_rows
            },
            "pricing_failure_reason_counts": {
                str(reason): int(count or 0) for reason, count in pricing_reason_rows
            },
            "v2_total_rows": v2_total,
            "v2_unique_conditions": v2_unique,
            "prediction_valid_rows": prediction_valid,
            "pricing_valid_rows": pricing_valid,
            "economic_eligible_rows": economic_eligible,
            "v2_pricing_coverage": str(v2_pricing_coverage),
            "valid_capture_start": None if v2_row is None else v2_row[5],
            "pre_repair_prediction_unavailable_rows": int(
                0 if legacy_unavailable is None else legacy_unavailable[0] or 0
            ),
            "timing_status_counts": {
                str(status): int(count or 0) for status, count in blocker_rows
            },
            "evaluation_state": evaluation_state,
            "marker": marker,
            "real_order_submission": False,
        }

    def recent_sol5m_prospective_evidence(
        self, *, limit: int = 20
    ) -> tuple[dict[str, object], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        bounded_limit = min(limit, 100)
        with sqlite3.connect(self._path) as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT evidence_id,condition_id,market_id,observed_at,
                           predicted_side,calibrated_probability,
                           selected_side_executable_cost,pricing_status,
                           COALESCE(prediction_status,'LEGACY_NOT_REPORTED'),
                           prediction_failure_reason,pricing_failure_reason,
                           COALESCE(label_status,'UNLABELED'),
                           economic_eligible,valid_capture_start,
                           timing_status,outcome_json,outcome_attached_at,
                           payload_json
                    FROM sol5m_prospective_evidence
                    ORDER BY observed_at DESC, condition_id ASC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
            except sqlite3.OperationalError:
                return ()
        samples: list[dict[str, object]] = []
        for row in rows:
            outcome = (
                None
                if row[15] is None
                else _label_from_outcome(_safe_json_object(str(row[15])))
            )
            payload = _safe_json_object(str(row[17] or "{}"))
            prediction_raw = payload.get("prediction")
            prediction_payload = (
                prediction_raw if isinstance(prediction_raw, dict) else {}
            )
            samples.append(
                {
                    "evidence_id": str(row[0]),
                    "condition_id": str(row[1]),
                    "market_id": str(row[2]),
                    "observed_at": str(row[3]),
                    "predicted_side": row[4],
                    "calibrated_probability": row[5],
                    "selected_side_executable_cost": row[6],
                    "pricing_status": str(row[7]),
                    "prediction_status": str(row[8]),
                    "prediction_failure_reason": row[9],
                    "pricing_failure_reason": row[10],
                    "label_status": str(row[11]),
                    "economic_eligible": bool(row[12]) if row[12] is not None else None,
                    "valid_capture_start": row[13],
                    "timing_status": str(row[14]),
                    "official_outcome": outcome,
                    "outcome_attached_at": row[16],
                    "model_id": payload.get("model_id"),
                    "artifact_checksum": payload.get("artifact_checksum"),
                    "raw_model_probability": prediction_payload.get("raw_model_probability"),
                    "up_executable_cost": payload.get("up_executable_cost"),
                    "down_executable_cost": payload.get("down_executable_cost"),
                }
            )
        return tuple(samples)

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
            totals_row = connection.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN outcome_json IS NOT NULL THEN 1 ELSE 0 END),
                       COUNT(DISTINCT condition_id),
                       COUNT(DISTINCT CASE WHEN outcome_json IS NOT NULL THEN condition_id END)
                FROM directional_checkpoint_observations
                """
            ).fetchone()
            multi_label_row = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT condition_id,COUNT(DISTINCT outcome_json) AS label_count
                    FROM directional_checkpoint_observations
                    WHERE outcome_json IS NOT NULL
                    GROUP BY condition_id
                    HAVING label_count > 1
                )
                """
            ).fetchone()
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
        total_rows = int((totals_row or (0, 0, 0, 0))[0] or 0)
        labeled_rows = int((totals_row or (0, 0, 0, 0))[1] or 0)
        unique_conditions = int((totals_row or (0, 0, 0, 0))[2] or 0)
        labeled_unique_conditions = int((totals_row or (0, 0, 0, 0))[3] or 0)
        unlabeled_rows = total_rows - labeled_rows
        unlabeled_unique_conditions = unique_conditions - labeled_unique_conditions
        return {
            "status": "DIRECTIONAL_CHECKPOINT_DATASET_READY",
            "checkpoint_targets_seconds": [120, 90, 60, 45],
            "checkpoint_tolerance_seconds": 10,
            "total_rows": total_rows,
            "labeled_rows": labeled_rows,
            "unlabeled_rows": unlabeled_rows,
            "unique_conditions": unique_conditions,
            "labeled_unique_conditions": labeled_unique_conditions,
            "unlabeled_unique_conditions": unlabeled_unique_conditions,
            "label_coverage_rows": str(labeled_rows / total_rows) if total_rows else "0",
            "label_coverage_conditions": (
                str(labeled_unique_conditions / unique_conditions)
                if unique_conditions
                else "0"
            ),
            "multi_label_condition_count": int((multi_label_row or (0,))[0] or 0),
            "buckets": buckets,
            "duplicate_reject_count": int((duplicate_rows or (0,))[0] or 0),
            "feature_schema_versions": {
                str(row[0]): int(row[1] or 0) for row in schema_rows
            },
            "missing_feature_counts": missing_counts,
            "feature_observation_counts": feature_counts,
            "future_timestamp_violations": future_timestamp_violations,
        }

    def recent_labeled_conditions(
        self,
        *,
        limit: int = 20,
        asset: Asset | None = None,
        horizon: Horizon | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Return bounded official-label proof grouped by condition.

        This is read-only operator evidence.  It deliberately returns unique
        conditions instead of raw checkpoint rows so one condition with four
        checkpoints cannot look like four independent labels.
        """

        if limit < 1:
            raise ValueError("limit must be positive")
        bounded_limit = min(limit, 100)
        clauses = ["outcome_json IS NOT NULL"]
        params: list[object] = []
        if asset is not None:
            clauses.append("asset=?")
            params.append(asset.value)
        if horizon is not None:
            clauses.append("horizon=?")
            params.append(horizon.value)
        where = " AND ".join(clauses)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                f"""
                SELECT condition_id,asset,horizon,
                       MIN(observed_at),
                       MAX(outcome_attached_at),
                       COUNT(*),
                       GROUP_CONCAT(DISTINCT feature_schema_version),
                       MIN(outcome_json),
                       MIN(payload_json)
                FROM directional_checkpoint_observations
                WHERE {where}
                GROUP BY condition_id,asset,horizon
                ORDER BY MAX(outcome_attached_at) DESC, condition_id ASC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            outcome = _safe_json_object(str(row[7] or "{}"))
            payload = _safe_json_object(str(row[8] or "{}"))
            official_outcome = _label_from_outcome(outcome)
            results.append(
                {
                    "condition_id": str(row[0]),
                    "market_id": _optional_text(payload.get("market_id")),
                    "asset": str(row[1]),
                    "horizon": str(row[2]),
                    "window_start": _iso_or_none(_optional_datetime(payload.get("window_start"))),
                    "window_end": _iso_or_none(_optional_datetime(payload.get("window_end"))),
                    "official_outcome": official_outcome,
                    "resolution_source": outcome.get("settlement_source")
                    or outcome.get("settlement_source_kind"),
                    "resolved_at": outcome.get("official_resolved_at"),
                    "label_attached_at": None if row[4] is None else str(row[4]),
                    "evidence_hash": outcome.get("evidence_hash"),
                    "checkpoint_row_count": int(row[5] or 0),
                    "feature_schema_versions": sorted(
                        item for item in str(row[6] or "").split(",") if item
                    ),
                    "dataset_schema_version": "directional_checkpoint_observations:v1",
                    "source_kind": outcome.get("settlement_source_kind"),
                }
            )
        return tuple(results)

    def training_readiness_report(self, *, now: datetime | None = None) -> dict[str, object]:
        """Return conservative training-readiness evidence by asset x horizon.

        The report is diagnostic only.  It never trains, promotes, opens PAPER
        trades, rewrites labels, or treats checkpoint rows as independent
        resolved markets.
        """

        observed_now = now or datetime.now(UTC)
        require_utc("now", observed_now)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT asset,horizon,condition_id,checkpoint_target_tte_seconds,
                       feature_schema_version,observed_at,actual_tte_seconds,
                       payload_json,outcome_json,outcome_attached_at
                FROM directional_checkpoint_observations
                ORDER BY observed_at ASC
                """
            ).fetchall()
        by_bucket: dict[tuple[str, str], dict[str, Any]] = {
            (asset.value, horizon.value): _empty_readiness_bucket(asset, horizon)
            for asset in Asset
            for horizon in Horizon
        }
        condition_state: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            asset_value = str(row[0])
            horizon_value = str(row[1])
            key = (asset_value, horizon_value)
            bucket = by_bucket.setdefault(
                key, _empty_readiness_bucket(Asset(asset_value), Horizon(horizon_value))
            )
            condition_id = str(row[2])
            target = int(row[3] or 0)
            feature_schema = str(row[4])
            observed_at = datetime.fromisoformat(str(row[5]))
            actual_tte = None if row[6] is None else int(row[6])
            payload = _safe_json_object(str(row[7]))
            outcome = _safe_json_object(str(row[8])) if row[8] is not None else None
            row_key = (asset_value, horizon_value, condition_id)
            state = condition_state.setdefault(
                row_key,
                {
                    "condition_id": condition_id,
                    "asset": asset_value,
                    "horizon": horizon_value,
                    "row_count": 0,
                    "targets": set(),
                    "labeled": False,
                    "labels": set(),
                    "window_end": _optional_datetime(payload.get("window_end")),
                    "first_observed_at": observed_at,
                    "last_observed_at": observed_at,
                },
            )
            state["row_count"] = int(state["row_count"]) + 1
            cast_targets = state["targets"]
            if isinstance(cast_targets, set):
                cast_targets.add(target)
            state["last_observed_at"] = observed_at
            if outcome is not None:
                state["labeled"] = True
                label = _label_from_outcome(outcome)
                if label is not None:
                    labels = state["labels"]
                    if isinstance(labels, set):
                        labels.add(label)
            bucket["row_count"] = _int_field(bucket, "row_count") + 1
            feature_schema_counts = cast(dict[str, int], bucket["feature_schema_counts"])
            feature_schema_counts[feature_schema] = (
                int(feature_schema_counts.get(feature_schema, 0)) + 1
            )
            if outcome is not None:
                bucket["labeled_row_count"] = _int_field(bucket, "labeled_row_count") + 1
                dataset_schema_counts = cast(dict[str, int], bucket["dataset_schema_counts"])
                dataset_schema_counts["directional_checkpoint_observations:v1"] = (
                    int(dataset_schema_counts.get(
                        "directional_checkpoint_observations:v1", 0
                    ))
                    + 1
                )
            if actual_tte is None:
                bucket["missing_actual_tte_count"] = (
                    _int_field(bucket, "missing_actual_tte_count") + 1
                )
            else:
                error = abs(actual_tte - target)
                timing = cast(dict[str, int], bucket["checkpoint_timing_error_seconds"])
                timing["count"] = int(timing.get("count", 0)) + 1
                timing["max"] = max(int(timing.get("max", 0)), error)
                timing["within_tolerance_count"] = (
                    int(timing.get("within_tolerance_count", 0)) + (1 if error <= 10 else 0)
                )
            target_counts = cast(dict[str, int], bucket["checkpoint_target_counts"])
            target_counts[str(target)] = int(target_counts.get(str(target), 0)) + 1
            _accumulate_feature_quality(
                bucket,
                payload,
                observed_at,
                outcome=outcome,
                checkpoint_target=target,
            )
        for state in condition_state.values():
            key = (str(state["asset"]), str(state["horizon"]))
            bucket = by_bucket[key]
            bucket["unique_conditions"] = _int_field(bucket, "unique_conditions") + 1
            first_observed_at = state.get("first_observed_at")
            last_observed_at = state.get("last_observed_at")
            if isinstance(first_observed_at, datetime):
                current = bucket.get("first_observed_at")
                bucket["first_observed_at"] = (
                    first_observed_at
                    if not isinstance(current, datetime) or first_observed_at < current
                    else current
                )
            if isinstance(last_observed_at, datetime):
                current = bucket.get("last_observed_at")
                bucket["last_observed_at"] = (
                    last_observed_at
                    if not isinstance(current, datetime) or last_observed_at > current
                    else current
                )
            labels_obj = state.get("labels")
            labels = labels_obj if isinstance(labels_obj, set) else set()
            if len(labels) > 1:
                bucket["official_label_conflict_count"] = (
                    _int_field(bucket, "official_label_conflict_count") + 1
                )
            if state.get("labeled"):
                bucket["labeled_unique_conditions"] = (
                    _int_field(bucket, "labeled_unique_conditions") + 1
                )
                if "UP" in labels:
                    bucket["up_labeled_conditions"] = (
                        _int_field(bucket, "up_labeled_conditions") + 1
                    )
                if "DOWN" in labels:
                    bucket["down_labeled_conditions"] = (
                        _int_field(bucket, "down_labeled_conditions") + 1
                    )
                continue
            window_end = state.get("window_end")
            if isinstance(window_end, datetime) and window_end <= observed_now:
                bucket["eligible_unlabeled_conditions"] = (
                    _int_field(bucket, "eligible_unlabeled_conditions") + 1
                )
            else:
                bucket["active_or_unknown_unlabeled_conditions"] = (
                    _int_field(bucket, "active_or_unknown_unlabeled_conditions") + 1
                )
        buckets = []
        blockers: list[str] = []
        for asset in Asset:
            for horizon in Horizon:
                bucket = by_bucket[(asset.value, horizon.value)]
                _finalize_readiness_bucket(bucket)
                buckets.append(bucket)
                if bucket["readiness_state"] != "TRAINING_READY":
                    blockers.append(f"{asset.value}-{horizon.value}:{bucket['readiness_state']}")
        return {
            "status": "DIRECTIONAL_CORPUS_READINESS_READY",
            "policy_version": READINESS_POLICY_VERSION,
            "generated_at": observed_now.isoformat(),
            "real_order_submission": False,
            "training_started": False,
            "model_promotion_changed": False,
            "paper_execution_permission_changed": False,
            "minimum_labeled_unique_conditions": MINIMUM_LABELED_UNIQUE_CONDITIONS,
            "minimum_class_count_per_side": MINIMUM_CLASS_COUNT_PER_SIDE,
            "minimum_eligible_label_coverage": str(MINIMUM_ELIGIBLE_LABEL_COVERAGE),
            "checkpoint_targets_seconds": list(CHECKPOINT_TARGETS_SECONDS),
            "checkpoint_tolerance_seconds": CHECKPOINT_TOLERANCE_SECONDS,
            "buckets": buckets,
            "training_ready_buckets": [
                f"{row['asset']}-{row['horizon']}"
                for row in buckets
                if row["readiness_state"] == "TRAINING_READY"
            ],
            "blockers": blockers[:50],
        }

    def pending_checkpoint_label_candidates(
        self, *, now: datetime, limit: int
    ) -> tuple[DirectionalCheckpointLabelCandidate, ...]:
        require_utc("now", now)
        if limit < 1:
            raise ValueError("limit must be positive")
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT condition_id,asset,horizon,payload_json,
                       COUNT(*) AS checkpoint_row_count,
                       SUM(CASE WHEN outcome_json IS NULL THEN 1 ELSE 0 END)
                           AS unlabeled_row_count,
                       MIN(observed_at) AS first_observed_at
                FROM directional_checkpoint_observations
                WHERE outcome_json IS NULL
                GROUP BY condition_id,asset,horizon
                ORDER BY first_observed_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        candidates: list[DirectionalCheckpointLabelCandidate] = []
        for row in rows:
            payload = json.loads(str(row[3]))
            if not isinstance(payload, dict):
                payload = {}
            candidates.append(
                DirectionalCheckpointLabelCandidate(
                    condition_id=str(row[0]),
                    asset=Asset(str(row[1])),
                    horizon=Horizon(str(row[2])),
                    market_id=_optional_text(payload.get("market_id")),
                    window_start=_optional_datetime(payload.get("window_start")),
                    window_end=_optional_datetime(payload.get("window_end")),
                    checkpoint_row_count=int(row[4] or 0),
                    unlabeled_row_count=int(row[5] or 0),
                )
            )
        return tuple(candidates)

    def unlabeled_checkpoint_conditions(
        self, *, limit: int = 5_000
    ) -> tuple[DirectionalUnlabeledCheckpointCondition, ...]:
        """Return unique checkpoint conditions that still need an official label.

        This is diagnostic/read-only evidence for label backlog health.  It is
        deliberately grouped by condition, not checkpoint row, so 120/90/60/45s
        observations for one market never inflate backlog counts.
        """

        if limit < 1:
            raise ValueError("limit must be positive")
        bounded_limit = min(limit, 5_000)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                """
                SELECT condition_id,asset,horizon,payload_json,
                       COUNT(*) AS checkpoint_row_count,
                       SUM(CASE WHEN outcome_json IS NULL THEN 1 ELSE 0 END)
                           AS unlabeled_row_count,
                       MIN(observed_at) AS first_observed_at,
                       MAX(observed_at) AS last_observed_at
                FROM directional_checkpoint_observations
                WHERE outcome_json IS NULL
                GROUP BY condition_id,asset,horizon
                ORDER BY first_observed_at ASC, condition_id ASC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        conditions: list[DirectionalUnlabeledCheckpointCondition] = []
        for row in rows:
            payload = _safe_json_object(str(row[3] or "{}"))
            first_observed_at = _optional_datetime(row[6])
            last_observed_at = _optional_datetime(row[7])
            if first_observed_at is None or last_observed_at is None:
                continue
            conditions.append(
                DirectionalUnlabeledCheckpointCondition(
                    condition_id=str(row[0]),
                    asset=Asset(str(row[1])),
                    horizon=Horizon(str(row[2])),
                    market_id=_optional_text(payload.get("market_id")),
                    window_start=_optional_datetime(payload.get("window_start")),
                    window_end=_optional_datetime(payload.get("window_end")),
                    first_observed_at=first_observed_at,
                    last_observed_at=last_observed_at,
                    checkpoint_row_count=int(row[4] or 0),
                    unlabeled_row_count=int(row[5] or 0),
                )
            )
        return tuple(conditions)

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


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value


def _iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _safe_json_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _label_from_outcome(outcome: Mapping[str, object]) -> str | None:
    winning_side = outcome.get("winning_side")
    if winning_side in {"UP", "DOWN"}:
        return str(winning_side)
    outcome_up = outcome.get("outcome_up")
    if outcome_up is True:
        return "UP"
    if outcome_up is False:
        return "DOWN"
    return None


def _empty_readiness_bucket(asset: Asset, horizon: Horizon) -> dict[str, Any]:
    return {
        "asset": asset.value,
        "horizon": horizon.value,
        "row_count": 0,
        "unique_conditions": 0,
        "labeled_row_count": 0,
        "labeled_unique_conditions": 0,
        "up_labeled_conditions": 0,
        "down_labeled_conditions": 0,
        "eligible_unlabeled_conditions": 0,
        "active_or_unknown_unlabeled_conditions": 0,
        "eligible_unique_conditions": 0,
        "label_coverage_eligible_conditions": "0",
        "checkpoint_target_counts": {},
        "checkpoint_timing_error_seconds": {
            "count": 0,
            "within_tolerance_count": 0,
            "max": 0,
        },
        "feature_schema_counts": {},
        "dataset_schema_counts": {},
        "missing_feature_counts": {},
        "feature_observation_counts": {},
        "placeholder_zero_features": [],
        "non_finite_feature_count": 0,
        "zero_variance_features": [],
        "variance_gate_features": sorted(REQUIRED_SIGNAL_FEATURES),
        "variance_failed_features": [],
        "failing_features": [],
        "feature_classifications": {},
        "feature_statistics": {},
        "checkpoint_feature_statistics": {},
        "outcome_feature_statistics": {},
        "all_failed_gates": [],
        "primary_reason": "COLLECTING",
        "future_timestamp_violations": 0,
        "missing_actual_tte_count": 0,
        "official_label_conflict_count": 0,
        "first_observed_at": None,
        "last_observed_at": None,
        "_feature_values": {},
        "_labeled_feature_values": {},
        "_checkpoint_feature_values": {},
        "_outcome_feature_values": {},
        "readiness_state": "COLLECTING",
    }


def _accumulate_feature_quality(
    bucket: dict[str, Any],
    payload: Mapping[str, object],
    observed_at: datetime,
    *,
    outcome: Mapping[str, object] | None = None,
    checkpoint_target: int | None = None,
) -> None:
    feature_vector = payload.get("feature_vector")
    if not isinstance(feature_vector, dict):
        _increment_nested_int(bucket, "missing_feature_counts", "feature_vector")
        return
    generated_at = _optional_datetime(feature_vector.get("generated_at")) or observed_at
    features = feature_vector.get("features")
    if not isinstance(features, list) or not features:
        _increment_nested_int(bucket, "missing_feature_counts", "features")
        return
    values_by_name = bucket["_feature_values"]
    if not isinstance(values_by_name, dict):
        values_by_name = {}
        bucket["_feature_values"] = values_by_name
    labeled_values_by_name = bucket["_labeled_feature_values"]
    if not isinstance(labeled_values_by_name, dict):
        labeled_values_by_name = {}
        bucket["_labeled_feature_values"] = labeled_values_by_name
    checkpoint_values_by_name = bucket["_checkpoint_feature_values"]
    if not isinstance(checkpoint_values_by_name, dict):
        checkpoint_values_by_name = {}
        bucket["_checkpoint_feature_values"] = checkpoint_values_by_name
    outcome_values_by_name = bucket["_outcome_feature_values"]
    if not isinstance(outcome_values_by_name, dict):
        outcome_values_by_name = {}
        bucket["_outcome_feature_values"] = outcome_values_by_name
    label = _label_from_outcome(outcome) if outcome is not None else None
    for item in features:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "UNKNOWN")
        _increment_nested_int(bucket, "feature_observation_counts", name)
        value = item.get("value")
        if value is None:
            _increment_nested_int(bucket, "missing_feature_counts", name)
        numeric = _optional_float(value)
        if numeric is None:
            if value is not None:
                bucket["non_finite_feature_count"] = (
                    _int_field(bucket, "non_finite_feature_count") + 1
                )
        else:
            values = values_by_name.setdefault(name, [])
            if isinstance(values, list):
                values.append(numeric)
            if outcome is not None:
                labeled_values = labeled_values_by_name.setdefault(name, [])
                if isinstance(labeled_values, list):
                    labeled_values.append(numeric)
            if checkpoint_target is not None:
                target_key = str(checkpoint_target)
                target_values = checkpoint_values_by_name.setdefault(target_key, {})
                if isinstance(target_values, dict):
                    values_for_target = target_values.setdefault(name, [])
                    if isinstance(values_for_target, list):
                        values_for_target.append(numeric)
            if label is not None:
                label_values = outcome_values_by_name.setdefault(label, {})
                if isinstance(label_values, dict):
                    values_for_label = label_values.setdefault(name, [])
                    if isinstance(values_for_label, list):
                        values_for_label.append(numeric)
        if name == "spot_perp_basis" and numeric == 0.0:
            placeholders = bucket["placeholder_zero_features"]
            if isinstance(placeholders, list) and name not in placeholders:
                placeholders.append(name)
        source_ts = _optional_datetime(item.get("source_ts"))
        if source_ts is not None and source_ts > generated_at:
            bucket["future_timestamp_violations"] = (
                _int_field(bucket, "future_timestamp_violations") + 1
            )


def _increment_nested_int(bucket: dict[str, Any], field: str, key: str) -> None:
    values = bucket[field]
    if not isinstance(values, dict):
        values = {}
        bucket[field] = values
    values[key] = int(values.get(key, 0)) + 1


def _int_field(bucket: Mapping[str, object], field: str) -> int:
    value = bucket.get(field, 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _finalize_readiness_bucket(bucket: dict[str, Any]) -> None:
    labeled = _int_field(bucket, "labeled_unique_conditions")
    eligible_unlabeled = _int_field(bucket, "eligible_unlabeled_conditions")
    eligible_total = labeled + eligible_unlabeled
    bucket["eligible_unique_conditions"] = eligible_total
    bucket["label_coverage_eligible_conditions"] = (
        str(labeled / eligible_total) if eligible_total else "0"
    )
    first = bucket.get("first_observed_at")
    last = bucket.get("last_observed_at")
    bucket["first_observed_at"] = _iso_or_none(first if isinstance(first, datetime) else None)
    bucket["last_observed_at"] = _iso_or_none(last if isinstance(last, datetime) else None)
    timing = bucket["checkpoint_timing_error_seconds"]
    if isinstance(timing, dict):
        count = int(timing.get("count", 0))
        within = int(timing.get("within_tolerance_count", 0))
        timing["within_tolerance_rate"] = str(within / count) if count else "0"
    values_by_name = bucket.pop("_feature_values", {})
    labeled_values_by_name = bucket.pop("_labeled_feature_values", {})
    checkpoint_values_by_name = bucket.pop("_checkpoint_feature_values", {})
    outcome_values_by_name = bucket.pop("_outcome_feature_values", {})
    zero_variance: list[str] = []
    variance_failed_features: list[str] = []
    feature_statistics: dict[str, object] = {}
    if isinstance(values_by_name, dict):
        for name, values in values_by_name.items():
            if not isinstance(values, list):
                continue
            name_text = str(name)
            stats = _numeric_distribution(values)
            feature_statistics[name_text] = {
                "classification": _feature_classification(name_text),
                "all_rows": stats,
                "labeled_rows": _numeric_distribution(
                    labeled_values_by_name.get(name_text, [])
                    if isinstance(labeled_values_by_name, dict)
                    else []
                ),
                "variance_gate": name_text in REQUIRED_SIGNAL_FEATURES,
                "placeholder_non_signal": name_text in PLACEHOLDER_NON_SIGNAL_FEATURES,
                "current_threshold": "unique_values > 1",
            }
            if len(values) > 1 and len(set(values)) <= 1:
                zero_variance.append(name_text)
                if name_text in REQUIRED_SIGNAL_FEATURES:
                    variance_failed_features.append(name_text)
    bucket["zero_variance_features"] = sorted(zero_variance)
    bucket["variance_failed_features"] = sorted(variance_failed_features)
    bucket["feature_statistics"] = feature_statistics
    bucket["feature_classifications"] = {
        name: _feature_classification(name)
        for name in sorted(set(feature_statistics) | PLACEHOLDER_NON_SIGNAL_FEATURES)
    }
    bucket["checkpoint_feature_statistics"] = _nested_distribution(checkpoint_values_by_name)
    bucket["outcome_feature_statistics"] = _nested_distribution(outcome_values_by_name)
    bucket["failing_features"] = [
        _failing_feature_detail(name, feature_statistics.get(name, {}))
        for name in sorted(variance_failed_features)
    ]
    failed_gates: list[str] = []
    if _int_field(bucket, "official_label_conflict_count") > 0 or _int_field(
        bucket, "future_timestamp_violations"
    ) > 0:
        failed_gates.append("DATA_INTEGRITY_BLOCKED")
    if len(bucket["feature_schema_counts"]) > 1:
        failed_gates.append("SCHEMA_INCOMPATIBLE")
    if _int_field(bucket, "non_finite_feature_count") > 0:
        failed_gates.append("FEATURE_COMPLETENESS_FAILED")
    if labeled <= 0:
        failed_gates.append("COLLECTING")
    if 0 < labeled < MINIMUM_LABELED_UNIQUE_CONDITIONS:
        failed_gates.append("INSUFFICIENT_UNIQUE_CONDITIONS")
    if labeled > 0 and (
        _int_field(bucket, "up_labeled_conditions") < MINIMUM_CLASS_COUNT_PER_SIDE
        or _int_field(bucket, "down_labeled_conditions") < MINIMUM_CLASS_COUNT_PER_SIDE
    ):
        failed_gates.append("SEVERE_CLASS_IMBALANCE")
    if eligible_total and labeled / eligible_total < MINIMUM_ELIGIBLE_LABEL_COVERAGE:
        failed_gates.append("LABEL_COVERAGE_INSUFFICIENT")
    target_counts = bucket["checkpoint_target_counts"]
    if not isinstance(target_counts, dict) or any(
        int(target_counts.get(str(target), 0)) <= 0 for target in CHECKPOINT_TARGETS_SECONDS
    ):
        failed_gates.append("CHECKPOINT_COVERAGE_INSUFFICIENT")
    if variance_failed_features and labeled >= 2:
        failed_gates.append("FEATURE_VARIANCE_FAILED")
    state = "TRAINING_READY"
    for candidate in (
        "DATA_INTEGRITY_BLOCKED",
        "SCHEMA_INCOMPATIBLE",
        "FEATURE_COMPLETENESS_FAILED",
        "COLLECTING",
        "INSUFFICIENT_UNIQUE_CONDITIONS",
        "SEVERE_CLASS_IMBALANCE",
        "LABEL_COVERAGE_INSUFFICIENT",
        "CHECKPOINT_COVERAGE_INSUFFICIENT",
        "FEATURE_VARIANCE_FAILED",
    ):
        if candidate in failed_gates:
            state = candidate
            break
    bucket["all_failed_gates"] = failed_gates
    bucket["primary_reason"] = state
    bucket["readiness_state"] = state


def _feature_classification(name: str) -> str:
    if name in PLACEHOLDER_NON_SIGNAL_FEATURES:
        return "PLACEHOLDER_NON_SIGNAL"
    if name in DERIVED_DIAGNOSTIC_FEATURES:
        return "DERIVED_DIAGNOSTIC"
    if name in REQUIRED_SIGNAL_FEATURES:
        return "REQUIRED_SIGNAL_FEATURE"
    return "UNKNOWN"


def _numeric_distribution(values: object) -> dict[str, object]:
    numeric_values = [
        float(value)
        for value in values
        if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
    ] if isinstance(values, list) else []
    count = len(numeric_values)
    if count == 0:
        return {
            "n": 0,
            "non_null_n": 0,
            "unique": 0,
            "mean": None,
            "stddev": None,
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
            "zero_count": 0,
            "non_zero_count": 0,
        }
    sorted_values = sorted(numeric_values)
    mean = sum(sorted_values) / count
    variance = sum((value - mean) ** 2 for value in sorted_values) / count
    zero_count = sum(1 for value in sorted_values if value == 0.0)
    return {
        "n": count,
        "non_null_n": count,
        "unique": len(set(sorted_values)),
        "mean": _finite_float(mean),
        "stddev": _finite_float(math.sqrt(variance)),
        "min": _finite_float(sorted_values[0]),
        "p10": _finite_float(_percentile(sorted_values, 0.10)),
        "p25": _finite_float(_percentile(sorted_values, 0.25)),
        "median": _finite_float(_percentile(sorted_values, 0.50)),
        "p75": _finite_float(_percentile(sorted_values, 0.75)),
        "p90": _finite_float(_percentile(sorted_values, 0.90)),
        "max": _finite_float(sorted_values[-1]),
        "zero_count": zero_count,
        "non_zero_count": count - zero_count,
    }


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _finite_float(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _nested_distribution(values_by_group: object) -> dict[str, dict[str, object]]:
    if not isinstance(values_by_group, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for group, values_by_name in values_by_group.items():
        if not isinstance(values_by_name, dict):
            continue
        result[str(group)] = {
            str(name): _numeric_distribution(values)
            for name, values in values_by_name.items()
        }
    return result


def _failing_feature_detail(name: str, stats_obj: object) -> dict[str, object]:
    stats = stats_obj if isinstance(stats_obj, dict) else {}
    rows_obj = stats.get("all_rows")
    all_rows = rows_obj if isinstance(rows_obj, dict) else {}
    return {
        "name": name,
        "classification": _feature_classification(name),
        "n": all_rows.get("n"),
        "unique": all_rows.get("unique"),
        "stddev": all_rows.get("stddev"),
        "threshold": "unique_values > 1",
        "gate_status": "FAIL",
    }


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
