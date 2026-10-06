"""P2.3 SOL-5m offline GO/NO-GO challenger experiment.

This module is intentionally offline/read-only.  It reads immutable checkpoint
observations, fits dependency-free research challengers, and emits a
non-promotable report.  It does not touch model governance, PAPER execution,
LIVE settings, or corpus labels.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from direction_engine_v3.domain import Asset, Horizon

FEATURE_SCHEMA_VERSION = "v3.15.3-directional-official-ptb"
DATASET_SCHEMA_VERSION = "directional_checkpoint_observations:v1"
TARGET_ASSET = Asset.SOL
TARGET_HORIZON = Horizon.FIVE_MINUTES
CHECKPOINT_TARGETS = (120, 90, 60, 45)
PLACEHOLDER_EXCLUDED_FEATURES = ("spot_perp_basis",)
REDUNDANT_EXCLUDED_FEATURES = ("signal_stability",)
FEATURE_NAMES = (
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
    "flip_rate",
    "regime_score",
)
REGULARIZATION_GRID = (("strong", 0.1), ("medium", 0.01), ("weak", 0.001))
MINIMUM_DEFAULT_FINAL_HOLDOUT_CONDITIONS = 30
MINIMUM_ECONOMIC_COVERAGE = 0.30
MAXIMUM_ACCEPTABLE_ECE = 0.08
NEAR_ZERO = 1e-12
BOOTSTRAP_RESAMPLES = 500
P2_3_SOL5M_PROSPECTIVE_CUTOFF = datetime.fromisoformat(
    "2026-10-06T02:43:07.465344+00:00"
)
P2_3_SOL5M_PROSPECTIVE_DATASET_FINGERPRINT = (
    "7759be2532b26d03f30c911bcba5928fab4521cd881528399e1613767f1e277f"
)
P2_3_SOL5M_ACCEPTED_CODE_SHA = "b36a50d402ff5b384272607fb7b8352ffca3207a"
P2_3_SOL5M_PROSPECTIVE_TARGET_CHECKPOINT = 45
P2_3_SOL5M_RESEARCH_NOTIONAL_USDC = 0.75


@dataclass(frozen=True, slots=True)
class P23Example:
    checkpoint_id: str
    condition_id: str
    target: int
    observed_at: datetime
    features: tuple[float, ...]
    outcome_up: bool
    executable_cost: float | None


@dataclass(frozen=True, slots=True)
class P23Condition:
    condition_id: str
    chronology: datetime
    outcome_up: bool
    examples_by_target: dict[int, P23Example]


@dataclass(frozen=True, slots=True)
class P23Dataset:
    code_sha: str
    fingerprint: str
    conditions: tuple[P23Condition, ...]
    excluded_rows: dict[str, int]
    leakage_violations: int
    label_conflicts: int
    checkpoint_counts: dict[int, int]
    earliest: str | None
    latest: str | None

    @property
    def unique_conditions(self) -> int:
        return len(self.conditions)

    @property
    def row_count(self) -> int:
        return sum(len(item.examples_by_target) for item in self.conditions)

    @property
    def up_count(self) -> int:
        return sum(1 for item in self.conditions if item.outcome_up)

    @property
    def down_count(self) -> int:
        return self.unique_conditions - self.up_count


@dataclass(frozen=True, slots=True)
class _Scaler:
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def transform(self, rows: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            tuple(
                (value - mean) / scale
                for value, mean, scale in zip(row, self.means, self.scales, strict=True)
            )
            for row in rows
        )


@dataclass(frozen=True, slots=True)
class _LogisticModel:
    coefficients: tuple[float, ...]
    intercept: float
    scaler: _Scaler

    def score(self, row: tuple[float, ...]) -> float:
        scaled = self.scaler.transform((row,))[0]
        return self.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.coefficients, scaled, strict=True)
        )

    def probability(self, row: tuple[float, ...]) -> float:
        return _sigmoid(self.score(row))


@dataclass(frozen=True, slots=True)
class _PlattCalibrator:
    coefficient: float
    intercept: float

    def calibrate_score(self, score: float) -> float:
        return _sigmoid(self.intercept + self.coefficient * score)


@dataclass(frozen=True, slots=True)
class P23Metrics:
    conditions: int
    up: int
    down: int
    accuracy: float
    brier: float
    log_loss: float
    ece: float
    mean_probability: float
    empirical_up_rate: float
    economic_coverage: float
    after_cost_ev: float | None
    replay_pnl: float | None
    maximum_drawdown: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "conditions": self.conditions,
            "up": self.up,
            "down": self.down,
            "accuracy": self.accuracy,
            "brier": self.brier,
            "log_loss": self.log_loss,
            "ece": self.ece,
            "mean_probability": self.mean_probability,
            "empirical_up_rate": self.empirical_up_rate,
            "economic_coverage": self.economic_coverage,
            "after_cost_ev": self.after_cost_ev,
            "replay_pnl": self.replay_pnl,
            "maximum_drawdown": self.maximum_drawdown,
        }


@dataclass(frozen=True, slots=True)
class P23Uncertainty:
    status: str
    method: str
    resamples: int
    economic_condition_count: int
    after_cost_ev_p05: float | None
    after_cost_ev_p50: float | None
    after_cost_ev_p95: float | None
    replay_pnl_p05: float | None
    replay_pnl_p50: float | None
    replay_pnl_p95: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "method": self.method,
            "resamples": self.resamples,
            "economic_condition_count": self.economic_condition_count,
            "after_cost_ev_p05": self.after_cost_ev_p05,
            "after_cost_ev_p50": self.after_cost_ev_p50,
            "after_cost_ev_p95": self.after_cost_ev_p95,
            "replay_pnl_p05": self.replay_pnl_p05,
            "replay_pnl_p50": self.replay_pnl_p50,
            "replay_pnl_p95": self.replay_pnl_p95,
        }


@dataclass(frozen=True, slots=True)
class P23FoldResult:
    fold: int
    checkpoint: int
    regularization_name: str
    train_conditions: int
    validation_conditions: int
    challenger: P23Metrics
    base_rate: P23Metrics
    coefficient_signs: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "fold": self.fold,
            "checkpoint": self.checkpoint,
            "regularization": self.regularization_name,
            "train_conditions": self.train_conditions,
            "validation_conditions": self.validation_conditions,
            "challenger": self.challenger.as_dict(),
            "base_rate": self.base_rate.as_dict(),
            "coefficient_signs": self.coefficient_signs,
        }


@dataclass(frozen=True, slots=True)
class P23Result:
    verdict: str
    marker: str
    dataset: P23Dataset
    selected_checkpoint: int | None
    selected_regularization: str | None
    final_challenger_spec: dict[str, object]
    development_folds: tuple[P23FoldResult, ...]
    final_challenger: P23Metrics | None
    final_base_rate: P23Metrics | None
    gate_table: list[dict[str, str]]
    failed_gates: tuple[str, ...]
    notes: tuple[str, ...]
    coefficient_summary: dict[str, object]
    uncertainty: P23Uncertainty | None
    final_evaluation_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "marker": self.marker,
            "dataset": _dataset_dict(self.dataset),
            "selected_checkpoint": self.selected_checkpoint,
            "selected_regularization": self.selected_regularization,
            "final_challenger_spec": self.final_challenger_spec,
            "development_folds": [fold.as_dict() for fold in self.development_folds],
            "final_challenger": None
            if self.final_challenger is None
            else self.final_challenger.as_dict(),
            "final_base_rate": None
            if self.final_base_rate is None
            else self.final_base_rate.as_dict(),
            "gate_table": self.gate_table,
            "failed_gates": list(self.failed_gates),
            "notes": list(self.notes),
            "coefficient_summary": self.coefficient_summary,
            "uncertainty": None if self.uncertainty is None else self.uncertainty.as_dict(),
            "final_evaluation_count": self.final_evaluation_count,
        }


@dataclass(frozen=True, slots=True)
class P23FrozenChallenger:
    status: str
    reason: str
    model_id: str
    artifact_checksum: str | None
    cutoff_observed_at: datetime
    dataset_fingerprint: str | None
    checkpoint: int | None
    selected_regularization: str | None
    l2_penalty: float | None
    feature_names: tuple[str, ...]
    model: _LogisticModel | None
    calibrator: _PlattCalibrator | None
    spec: dict[str, object]

    @property
    def ready(self) -> bool:
        return self.status == "READY" and self.model is not None

    def predict(self, features: tuple[float, ...]) -> dict[str, object]:
        if not self.ready or self.model is None:
            return {
                "status": "FROZEN_CHALLENGER_UNAVAILABLE",
                "reason": self.reason,
                "model_id": self.model_id,
            }
        if len(features) != len(self.feature_names):
            return {
                "status": "FEATURE_ORDER_MISMATCH",
                "reason": "FEATURE_COUNT_MISMATCH",
                "model_id": self.model_id,
            }
        raw_probability = self.model.probability(features)
        raw_score = self.model.score(features)
        calibrated_probability = (
            self.calibrator.calibrate_score(raw_score)
            if self.calibrator is not None
            else raw_probability
        )
        predicted_side = "UP" if calibrated_probability >= 0.5 else "DOWN"
        return {
            "status": "READY",
            "reason": "FROZEN_P2_3_CHALLENGER_READY",
            "model_id": self.model_id,
            "artifact_checksum": self.artifact_checksum,
            "raw_model_probability": raw_probability,
            "calibrated_probability": calibrated_probability,
            "predicted_side": predicted_side,
            "selected_probability": calibrated_probability
            if predicted_side == "UP"
            else 1.0 - calibrated_probability,
        }


def load_sol5m_dataset(
    db_path: Path,
    *,
    code_sha: str,
    max_observed_at: datetime | None = None,
) -> P23Dataset:
    """Load the immutable SOL-5m checkpoint dataset from SQLite in read-only mode."""

    if max_observed_at is not None and max_observed_at.tzinfo is None:
        raise ValueError("max_observed_at must be timezone-aware")
    rows = _checkpoint_rows(db_path, max_observed_at=max_observed_at)
    excluded: dict[str, int] = {}
    leakage_violations = 0
    by_condition: dict[str, dict[str, Any]] = {}
    for row in rows:
        checkpoint_id = str(row["checkpoint_id"])
        condition_id = str(row["condition_id"])
        target = int(row["checkpoint_target_tte_seconds"])
        observed_at = _parse_datetime(str(row["observed_at"]))
        if str(row["feature_schema_version"]) != FEATURE_SCHEMA_VERSION:
            _increment(excluded, "FEATURE_SCHEMA_MISMATCH")
            continue
        if target not in CHECKPOINT_TARGETS:
            _increment(excluded, "UNSUPPORTED_CHECKPOINT")
            continue
        payload = _json_object(str(row["payload_json"]))
        outcome = _json_object(str(row["outcome_json"]))
        if outcome.get("settlement_source_kind") != "OFFICIAL":
            _increment(excluded, "NON_OFFICIAL_OUTCOME")
            continue
        outcome_up = outcome.get("outcome_up")
        if not isinstance(outcome_up, bool):
            _increment(excluded, "OUTCOME_MISSING")
            continue
        resolved_at = _optional_datetime(outcome.get("official_resolved_at"))
        if resolved_at is not None and resolved_at <= observed_at:
            leakage_violations += 1
            _increment(excluded, "OFFICIAL_OUTCOME_NOT_AFTER_OBSERVATION")
            continue
        feature_vector = payload.get("feature_vector")
        if not isinstance(feature_vector, dict):
            _increment(excluded, "FEATURE_VECTOR_MISSING")
            continue
        if feature_vector.get("feature_set_version") != FEATURE_SCHEMA_VERSION:
            _increment(excluded, "FEATURE_SET_MISMATCH")
            continue
        generated_at = _optional_datetime(feature_vector.get("generated_at"))
        if generated_at is not None and generated_at > observed_at:
            leakage_violations += 1
            _increment(excluded, "FEATURE_GENERATED_AFTER_OBSERVATION")
            continue
        feature_map = _feature_map(feature_vector.get("features"))
        row_values: list[float] = []
        bad_reason = None
        for name in FEATURE_NAMES:
            value = feature_map.get(name)
            if value is None:
                bad_reason = f"MISSING_FEATURE:{name}"
                break
            source_ts = value[1]
            if source_ts is not None and source_ts > observed_at:
                leakage_violations += 1
                bad_reason = f"FUTURE_SOURCE_TS:{name}"
                break
            row_values.append(value[0])
        if bad_reason is not None:
            _increment(excluded, bad_reason)
            continue
        state = by_condition.setdefault(
            condition_id,
            {
                "condition_id": condition_id,
                "chronology": observed_at,
                "outcome_up": outcome_up,
                "examples_by_target": {},
                "label_conflict": False,
            },
        )
        if bool(state["outcome_up"]) != outcome_up:
            state["label_conflict"] = True
            continue
        state["chronology"] = min(state["chronology"], observed_at)
        examples = state["examples_by_target"]
        if not isinstance(examples, dict):
            raise TypeError("examples_by_target must be dict")
        examples[target] = P23Example(
            checkpoint_id=checkpoint_id,
            condition_id=condition_id,
            target=target,
            observed_at=observed_at,
            features=tuple(row_values),
            outcome_up=outcome_up,
            executable_cost=_extract_executable_cost(payload),
        )
    label_conflicts = sum(1 for item in by_condition.values() if bool(item["label_conflict"]))
    conditions = tuple(
        sorted(
            (
                P23Condition(
                    condition_id=str(item["condition_id"]),
                    chronology=item["chronology"],
                    outcome_up=bool(item["outcome_up"]),
                    examples_by_target=dict(item["examples_by_target"]),
                )
                for item in by_condition.values()
                if not bool(item["label_conflict"]) and item["examples_by_target"]
            ),
            key=lambda item: (item.chronology, item.condition_id),
        )
    )
    checkpoint_counts = {
        target: sum(1 for item in conditions if target in item.examples_by_target)
        for target in CHECKPOINT_TARGETS
    }
    earliest = None if not conditions else conditions[0].chronology.isoformat()
    latest = None if not conditions else conditions[-1].chronology.isoformat()
    return P23Dataset(
        code_sha=code_sha,
        fingerprint=_dataset_fingerprint(code_sha, conditions),
        conditions=conditions,
        excluded_rows=excluded,
        leakage_violations=leakage_violations,
        label_conflicts=label_conflicts,
        checkpoint_counts=checkpoint_counts,
        earliest=earliest,
        latest=latest,
    )


def materialize_sol5m_frozen_challenger(
    db_path: Path,
    *,
    code_sha: str,
    cutoff_observed_at: datetime = P2_3_SOL5M_PROSPECTIVE_CUTOFF,
) -> P23FrozenChallenger:
    """Build the frozen P2.3 SOL-5m research challenger from pre-cutoff data only."""

    cutoff = cutoff_observed_at.astimezone(UTC)
    dataset = load_sol5m_dataset(
        db_path,
        code_sha=code_sha,
        max_observed_at=cutoff,
    )
    model_id = "P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY"
    base_spec: dict[str, object] = {
        "artifact_status": "RESEARCH_ONLY",
        "promotion_status": "NON_PROMOTABLE",
        "execution_permission": "NONE",
        "governance_rejection_reason": "MODEL_NOT_PROMOTED",
        "asset": TARGET_ASSET.value,
        "horizon": TARGET_HORIZON.value,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "dataset_schema": DATASET_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "excluded_features": {
            "placeholder_non_signal": list(PLACEHOLDER_EXCLUDED_FEATURES),
            "deterministic_duplicate": list(REDUNDANT_EXCLUDED_FEATURES),
        },
        "cutoff_observed_at": cutoff.isoformat(),
        "expected_p2_3_dataset_fingerprint": P2_3_SOL5M_PROSPECTIVE_DATASET_FINGERPRINT,
        "research_notional_usdc": str(P2_3_SOL5M_RESEARCH_NOTIONAL_USDC),
    }
    if dataset.unique_conditions == 0:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "NO_PRE_CUTOFF_SOL_5M_DATASET",
            model_id,
            None,
            cutoff,
            None,
            None,
            None,
            None,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    if dataset.fingerprint != P2_3_SOL5M_PROSPECTIVE_DATASET_FINGERPRINT:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "P2_3_DATASET_FINGERPRINT_MISMATCH",
            model_id,
            None,
            cutoff,
            dataset.fingerprint,
            None,
            None,
            None,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    if dataset.leakage_violations or dataset.label_conflicts:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "P2_3_DATASET_INTEGRITY_BLOCKED",
            model_id,
            None,
            cutoff,
            dataset.fingerprint,
            None,
            None,
            None,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    dev, final = _chronological_holdout(dataset.conditions)
    candidate_scores: list[tuple[float, float, int, str, float]] = []
    for target in CHECKPOINT_TARGETS:
        target_dev = tuple(item for item in dev if target in item.examples_by_target)
        folds = _walk_forward_condition_folds(target_dev)
        for regularization_name, penalty in REGULARIZATION_GRID:
            fold_results = [
                result
                for index, (train, validation) in enumerate(folds, start=1)
                if (
                    result := _evaluate_fold(
                        index,
                        target,
                        regularization_name,
                        penalty,
                        train,
                        validation,
                    )
                )
                is not None
            ]
            if fold_results:
                average_log_loss = sum(item.challenger.log_loss for item in fold_results) / len(
                    fold_results
                )
                average_brier = sum(item.challenger.brier for item in fold_results) / len(
                    fold_results
                )
                candidate_scores.append(
                    (average_log_loss, average_brier, target, regularization_name, penalty)
                )
    if not candidate_scores:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "DEVELOPMENT_WALK_FORWARD_INSUFFICIENT",
            model_id,
            None,
            cutoff,
            dataset.fingerprint,
            None,
            None,
            None,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    candidate_scores.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    _, _, selected_target, selected_regularization_name, selected_penalty = candidate_scores[0]
    if selected_target != P2_3_SOL5M_PROSPECTIVE_TARGET_CHECKPOINT:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "P2_3_SELECTED_CHECKPOINT_NOT_45S",
            model_id,
            None,
            cutoff,
            dataset.fingerprint,
            selected_target,
            selected_regularization_name,
            selected_penalty,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    model, calibrator = _fit_model_with_dev_calibration(
        dev,
        selected_target,
        selected_penalty,
    )
    if model is None:
        return P23FrozenChallenger(
            "UNAVAILABLE",
            "FINAL_DEV_ONLY_MODEL_FIT_INSUFFICIENT",
            model_id,
            None,
            cutoff,
            dataset.fingerprint,
            selected_target,
            selected_regularization_name,
            selected_penalty,
            FEATURE_NAMES,
            None,
            None,
            {**base_spec, "dataset_fingerprint": dataset.fingerprint},
        )
    spec: dict[str, object] = {
        **base_spec,
        "dataset_fingerprint": dataset.fingerprint,
        "checkpoint": selected_target,
        "selected_regularization": selected_regularization_name,
        "l2_penalty": selected_penalty,
        "development_unique_conditions": len(dev),
        "held_out_p2_3_final_conditions_not_used_for_fit": len(final),
        "selection_source": "P2_3_DEVELOPMENT_WALK_FORWARD_ONLY",
        "future_conditions_used_for_training": False,
        "calibration": "PLATT_LOGISTIC_DEV_ONLY",
        "calibration_available": calibrator is not None,
        "replay_rule": "calibrated_probability >= 0.5 selects UP else DOWN",
    }
    checksum_payload = {
        **spec,
        "model": {
            "family": "L2_LOGISTIC_DEPENDENCY_FREE",
            "coefficients": model.coefficients,
            "intercept": model.intercept,
            "scaler_means": model.scaler.means,
            "scaler_scales": model.scaler.scales,
            "platt_coefficient": None if calibrator is None else calibrator.coefficient,
            "platt_intercept": None if calibrator is None else calibrator.intercept,
        },
    }
    artifact_checksum = hashlib.sha256(
        json.dumps(checksum_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return P23FrozenChallenger(
        "READY",
        "FROZEN_P2_3_CHALLENGER_READY",
        model_id,
        artifact_checksum,
        cutoff,
        dataset.fingerprint,
        selected_target,
        selected_regularization_name,
        selected_penalty,
        FEATURE_NAMES,
        model,
        calibrator,
        {**spec, "artifact_checksum": artifact_checksum, "model_id": model_id},
    )


def run_sol5m_go_no_go(
    db_path: Path,
    *,
    code_sha: str,
    minimum_final_conditions: int = MINIMUM_DEFAULT_FINAL_HOLDOUT_CONDITIONS,
) -> P23Result:
    dataset = load_sol5m_dataset(db_path, code_sha=code_sha)
    if dataset.unique_conditions == 0:
        return _insufficient(dataset, "NO_SOL_5M_OFFICIAL_LABELED_CONDITIONS")
    if dataset.leakage_violations or dataset.label_conflicts:
        return _no_go(dataset, ("DATA_INTEGRITY_BLOCKED",))
    if dataset.up_count == 0 or dataset.down_count == 0:
        return _insufficient(dataset, "SINGLE_CLASS_DATASET")
    dev, final = _chronological_holdout(dataset.conditions)
    final_up = sum(1 for item in final if item.outcome_up)
    final_down = len(final) - final_up
    if len(final) < minimum_final_conditions or final_up == 0 or final_down == 0:
        return _insufficient(dataset, "FINAL_HOLDOUT_NOT_ADEQUATE")
    all_fold_results: list[P23FoldResult] = []
    candidate_scores: list[tuple[float, float, int, str, float]] = []
    for target in CHECKPOINT_TARGETS:
        target_dev = tuple(item for item in dev if target in item.examples_by_target)
        folds = _walk_forward_condition_folds(target_dev)
        for regularization_name, penalty in REGULARIZATION_GRID:
            fold_results = []
            for index, (train, validation) in enumerate(folds, start=1):
                result = _evaluate_fold(
                    index,
                    target,
                    regularization_name,
                    penalty,
                    train,
                    validation,
                )
                if result is not None:
                    fold_results.append(result)
                    all_fold_results.append(result)
            if fold_results:
                average_log_loss = sum(item.challenger.log_loss for item in fold_results) / len(
                    fold_results
                )
                average_brier = sum(item.challenger.brier for item in fold_results) / len(
                    fold_results
                )
                candidate_scores.append(
                    (average_log_loss, average_brier, target, regularization_name, penalty)
                )
    if not candidate_scores:
        return _insufficient(dataset, "DEVELOPMENT_WALK_FORWARD_INSUFFICIENT")
    candidate_scores.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    _, _, selected_target, selected_regularization_name, selected_penalty = candidate_scores[0]
    frozen_policy = _frozen_policy(
        dataset,
        dev,
        final,
        selected_target,
        selected_regularization_name,
        selected_penalty,
    )
    (
        final_challenger,
        final_base,
        final_model,
        final_calibrator,
        uncertainty,
    ) = _evaluate_final(
        dev,
        final,
        selected_target,
        selected_penalty,
    )
    if final_challenger is None or final_base is None or final_model is None:
        return _insufficient(dataset, "FINAL_MODEL_FIT_INSUFFICIENT")
    coefficient_summary = _coefficient_summary(final_model, all_fold_results)
    spec: dict[str, object] = {
        "artifact_status": "RESEARCH_ONLY",
        "asset": TARGET_ASSET.value,
        "horizon": TARGET_HORIZON.value,
        "dataset_fingerprint": dataset.fingerprint,
        "checkpoint": selected_target,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "dataset_schema": DATASET_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "excluded_features": {
            "placeholder_non_signal": list(PLACEHOLDER_EXCLUDED_FEATURES),
            "deterministic_duplicate": list(REDUNDANT_EXCLUDED_FEATURES),
        },
        "model": "L2_LOGISTIC_DEPENDENCY_FREE",
        "regularization": selected_regularization_name,
        "l2_penalty": selected_penalty,
        "calibration": "PLATT_LOGISTIC_DEV_ONLY",
        "calibration_available": final_calibrator is not None,
        "frozen_policy": frozen_policy,
        "frozen_before_final_holdout": True,
        "selection_source": "development_walk_forward_only",
        "final_holdout_evaluation_count": 1,
        "execution_permission": "NONE",
    }
    failed_gates = _failed_gates(final_challenger, final_base, all_fold_results)
    gate_table = _gate_table(final_challenger, final_base, all_fold_results, failed_gates)
    if "INSUFFICIENT_ECONOMIC_PRICING_COVERAGE" in failed_gates:
        marker = "P2_3_SOL_5M_INSUFFICIENT_EVIDENCE"
        verdict = "INSUFFICIENT_EVIDENCE"
    elif failed_gates:
        marker = "P2_3_SOL_5M_NO_GO"
        verdict = "NO_GO"
    else:
        marker = "P2_3_SOL_5M_GO"
        verdict = "GO"
    return P23Result(
        verdict=verdict,
        marker=marker,
        dataset=dataset,
        selected_checkpoint=selected_target,
        selected_regularization=selected_regularization_name,
        final_challenger_spec=spec,
        development_folds=tuple(all_fold_results),
        final_challenger=final_challenger,
        final_base_rate=final_base,
        gate_table=gate_table,
        failed_gates=tuple(failed_gates),
        notes=(
            "RESEARCH_BASELINE_COMPARISON_UNAVAILABLE",
            "TREE_CHALLENGER_SKIPPED_NO_DEPENDENCY",
            "NO_GOVERNANCE_OR_EXECUTION_CHANGE",
        ),
        coefficient_summary=coefficient_summary,
        uncertainty=uncertainty,
        final_evaluation_count=1,
    )


def render_markdown_report(result: P23Result) -> str:
    dataset = result.dataset
    lines = [
        "# P2.3 SOL-5m Offline GO/NO-GO",
        "",
        "## 1. Executive verdict",
        "",
        f"{result.verdict} — `{result.marker}`",
        "",
        "## 2. Dataset",
        "",
        f"- Code SHA: `{dataset.code_sha}`",
        f"- Dataset fingerprint: `{dataset.fingerprint}`",
        f"- Feature schema: `{FEATURE_SCHEMA_VERSION}`",
        f"- Dataset schema: `{DATASET_SCHEMA_VERSION}`",
        f"- Unique conditions: {dataset.unique_conditions}",
        f"- Rows: {dataset.row_count}",
        f"- UP / DOWN: {dataset.up_count} / {dataset.down_count}",
        f"- Chronological span: {dataset.earliest} → {dataset.latest}",
        f"- Checkpoint coverage: {dataset.checkpoint_counts}",
        f"- Excluded rows: {dataset.excluded_rows}",
        f"- Leakage violations: {dataset.leakage_violations}",
        f"- Label conflicts: {dataset.label_conflicts}",
        "",
        "## 3. Frozen feature list",
        "",
        f"- Used: {', '.join(FEATURE_NAMES)}",
        f"- Excluded placeholder: {', '.join(PLACEHOLDER_EXCLUDED_FEATURES)}",
        f"- Excluded deterministic duplicate: {', '.join(REDUNDANT_EXCLUDED_FEATURES)}",
        "",
        "## 4. Development walk-forward",
        "",
        "| Fold | Train | Validation | Checkpoint | Regularization | Brier | LogLoss | "
        "ECE | After-Cost EV | Candidate Count |",
        "|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for fold in result.development_folds:
        ev = fold.challenger.after_cost_ev
        lines.append(
            "| "
            f"{fold.fold} | {fold.train_conditions} | {fold.validation_conditions} | "
            f"{fold.checkpoint} | {fold.regularization_name} | "
            f"{fold.challenger.brier:.6f} | {fold.challenger.log_loss:.6f} | "
            f"{fold.challenger.ece:.6f} | "
            f"{'NA' if ev is None else f'{ev:.6f}'} | "
            f"{round(fold.challenger.economic_coverage * fold.challenger.conditions)} |"
        )
    lines.extend(
        [
            "",
            "## 5. Final challenger spec",
            "",
            "```json",
            json.dumps(result.final_challenger_spec, indent=2, sort_keys=True),
            "```",
            "",
            "## 6. Final holdout benchmark table",
            "",
            "| Model | Conditions | Accuracy | Brier | LogLoss | ECE | Economic Coverage | "
            "After-Cost EV | Replay PnL |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if result.final_base_rate is not None:
        lines.append(_metrics_row("BASE_RATE_BENCHMARK", result.final_base_rate))
    if result.final_challenger is not None:
        lines.append(_metrics_row("FINAL_CHALLENGER", result.final_challenger))
    lines.extend(
        [
            "",
            "## 7. Condition-level uncertainty",
            "",
            "```json",
            json.dumps(
                None if result.uncertainty is None else result.uncertainty.as_dict(),
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## 8. Coefficients",
            "",
            "```json",
            json.dumps(result.coefficient_summary, indent=2, sort_keys=True),
            "```",
            "",
            "## 9. GO gate table",
            "",
            "| Gate | Result | Evidence |",
            "|---|---|---|",
        ]
    )
    for gate in result.gate_table:
        lines.append(f"| {gate['gate']} | {gate['result']} | {gate['evidence']} |")
    lines.extend(
        [
            "",
            "## 10. Failed gates",
            "",
            ", ".join(result.failed_gates) if result.failed_gates else "None",
            "",
            "## 11. Model governance / safety",
            "",
            "- Artifact status: RESEARCH_ONLY",
            "- execution_permission: NONE",
            "- governance_rejection_reason: MODEL_NOT_PROMOTED",
            "- PAPER execution: unchanged / not enabled",
            "- LIVE execution: unchanged / not enabled",
            f"- final_holdout_evaluation_count: {result.final_evaluation_count}",
            "",
            "## 12. Exact next action",
            "",
            _next_action(result.verdict),
        ]
    )
    return "\n".join(lines) + "\n"


def _checkpoint_rows(
    db_path: Path,
    *,
    max_observed_at: datetime | None = None,
) -> tuple[sqlite3.Row, ...]:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        try:
            clauses = ["asset=?", "horizon=?", "outcome_json IS NOT NULL"]
            params: list[object] = [TARGET_ASSET.value, TARGET_HORIZON.value]
            if max_observed_at is not None:
                clauses.append("observed_at<=?")
                params.append(max_observed_at.isoformat())
            where = " AND ".join(clauses)
            return tuple(
                connection.execute(
                    f"""
                    SELECT checkpoint_id,condition_id,checkpoint_target_tte_seconds,
                           feature_schema_version,observed_at,actual_tte_seconds,
                           payload_json,outcome_json,outcome_attached_at
                    FROM directional_checkpoint_observations
                    WHERE {where}
                    ORDER BY observed_at ASC, condition_id ASC,
                             checkpoint_target_tte_seconds ASC
                    """,
                    tuple(params),
                ).fetchall()
            )
        except sqlite3.OperationalError:
            return ()


def _json_object(raw: str) -> dict[str, object]:
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _feature_map(features: object) -> dict[str, tuple[float, datetime | None]]:
    result: dict[str, tuple[float, datetime | None]] = {}
    if not isinstance(features, list):
        return result
    for item in features:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        value = _finite_float(item.get("value"))
        if value is None:
            continue
        result[name] = (value, _optional_datetime(item.get("source_ts")))
    return result


def _extract_executable_cost(payload: dict[str, object]) -> float | None:
    for key in (
        "selected_side_executable_cost",
        "selected_side_cost",
        "executable_cost",
        "all_in_cost_per_share",
    ):
        value = _finite_float(payload.get(key))
        if value is not None and 0 < value < 1:
            return value
    for nested_key in ("pricing", "edge", "execution_context", "directional_assessment"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            value = _extract_executable_cost(nested)
            if value is not None:
                return value
    return None


def _dataset_fingerprint(code_sha: str, conditions: tuple[P23Condition, ...]) -> str:
    payload: list[dict[str, object]] = []
    for condition in conditions:
        payload.append(
            {
                "condition_id": condition.condition_id,
                "chronology": condition.chronology.isoformat(),
                "outcome_up": condition.outcome_up,
                "targets": {
                    str(target): {
                        "checkpoint_id": example.checkpoint_id,
                        "observed_at": example.observed_at.isoformat(),
                        "features": example.features,
                    }
                    for target, example in sorted(condition.examples_by_target.items())
                },
            }
        )
    encoded = json.dumps(
        {
            "code_sha": code_sha,
            "asset": TARGET_ASSET.value,
            "horizon": TARGET_HORIZON.value,
            "feature_schema": FEATURE_SCHEMA_VERSION,
            "dataset_schema": DATASET_SCHEMA_VERSION,
            "feature_names": FEATURE_NAMES,
            "conditions": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _chronological_holdout(
    conditions: tuple[P23Condition, ...],
) -> tuple[tuple[P23Condition, ...], tuple[P23Condition, ...]]:
    split = max(1, int(len(conditions) * 0.8))
    if split >= len(conditions):
        split = len(conditions) - 1
    return conditions[:split], conditions[split:]


def _walk_forward_condition_folds(
    conditions: tuple[P23Condition, ...],
) -> tuple[tuple[tuple[P23Condition, ...], tuple[P23Condition, ...]], ...]:
    count = len(conditions)
    if count >= 160:
        fold_count = 4
    elif count >= 90:
        fold_count = 3
    elif count >= 30:
        fold_count = 2
    else:
        return ()
    folds = []
    for index in range(1, fold_count + 1):
        train_end = int(count * index / (fold_count + 1))
        validation_end = int(count * (index + 1) / (fold_count + 1))
        train = conditions[:train_end]
        validation = conditions[train_end:validation_end]
        if train and validation:
            folds.append((train, validation))
    return tuple(folds)


def _evaluate_fold(
    fold: int,
    target: int,
    regularization_name: str,
    penalty: float,
    train_conditions: tuple[P23Condition, ...],
    validation_conditions: tuple[P23Condition, ...],
) -> P23FoldResult | None:
    model, calibrator = _fit_model_with_dev_calibration(train_conditions, target, penalty)
    if model is None:
        return None
    examples = _examples(validation_conditions, target)
    if not examples:
        return None
    challenger_probabilities = tuple(
        calibrator.calibrate_score(model.score(example.features))
        if calibrator is not None
        else model.probability(example.features)
        for example in examples
    )
    base_rate = _base_rate(_examples(train_conditions, target))
    base_probabilities = tuple(base_rate for _ in examples)
    challenger = _metrics(examples, challenger_probabilities)
    base = _metrics(examples, base_probabilities)
    return P23FoldResult(
        fold=fold,
        checkpoint=target,
        regularization_name=regularization_name,
        train_conditions=len(train_conditions),
        validation_conditions=len(validation_conditions),
        challenger=challenger,
        base_rate=base,
        coefficient_signs=_coefficient_signs(model.coefficients),
    )


def _evaluate_final(
    dev_conditions: tuple[P23Condition, ...],
    final_conditions: tuple[P23Condition, ...],
    target: int,
    penalty: float,
) -> tuple[
    P23Metrics | None,
    P23Metrics | None,
    _LogisticModel | None,
    _PlattCalibrator | None,
    P23Uncertainty | None,
]:
    model, calibrator = _fit_model_with_dev_calibration(dev_conditions, target, penalty)
    if model is None:
        return None, None, None, None, None
    final_examples = _examples(final_conditions, target)
    dev_examples = _examples(dev_conditions, target)
    if not final_examples or not dev_examples:
        return None, None, None, None, None
    challenger_probabilities = tuple(
        calibrator.calibrate_score(model.score(example.features))
        if calibrator is not None
        else model.probability(example.features)
        for example in final_examples
    )
    base_probability = _base_rate(dev_examples)
    base_probabilities = tuple(base_probability for _ in final_examples)
    return (
        _metrics(final_examples, challenger_probabilities),
        _metrics(final_examples, base_probabilities),
        model,
        calibrator,
        _bootstrap_uncertainty(final_examples, challenger_probabilities),
    )


def _frozen_policy(
    dataset: P23Dataset,
    dev_conditions: tuple[P23Condition, ...],
    final_conditions: tuple[P23Condition, ...],
    selected_target: int,
    selected_regularization_name: str,
    selected_penalty: float,
) -> dict[str, object]:
    return {
        "policy_status": "FROZEN_BEFORE_FINAL_HOLDOUT",
        "dataset_fingerprint": dataset.fingerprint,
        "development_unique_conditions": len(dev_conditions),
        "final_holdout_unique_conditions": len(final_conditions),
        "final_holdout_earliest": None
        if not final_conditions
        else final_conditions[0].chronology.isoformat(),
        "final_holdout_latest": None
        if not final_conditions
        else final_conditions[-1].chronology.isoformat(),
        "selected_checkpoint": selected_target,
        "selected_regularization": selected_regularization_name,
        "l2_penalty": selected_penalty,
        "selection_source": "development_walk_forward_only",
        "feature_names": list(FEATURE_NAMES),
        "excluded_features": {
            "placeholder_non_signal": list(PLACEHOLDER_EXCLUDED_FEATURES),
            "deterministic_duplicate": list(REDUNDANT_EXCLUDED_FEATURES),
        },
        "calibration": "PLATT_LOGISTIC_DEV_ONLY",
        "replay_rule": "calibrated_probability >= 0.5 selects UP else DOWN",
        "after_cost_ev_rule": (
            "selected_calibrated_probability - checkpoint_time_executable_cost"
        ),
        "replay_pnl_rule": (
            "1 - checkpoint_time_executable_cost if selected side wins else "
            "-checkpoint_time_executable_cost"
        ),
        "go_gate_thresholds": {
            "maximum_ece": MAXIMUM_ACCEPTABLE_ECE,
            "minimum_economic_coverage": MINIMUM_ECONOMIC_COVERAGE,
            "minimum_final_holdout_conditions": MINIMUM_DEFAULT_FINAL_HOLDOUT_CONDITIONS,
            "requires_brier_better_than_base_rate": True,
            "requires_logloss_better_than_base_rate": True,
            "requires_positive_after_cost_ev": True,
            "requires_walk_forward_stability": True,
        },
        "final_holdout_evaluation_count_limit": 1,
    }


def _fit_model_with_dev_calibration(
    conditions: tuple[P23Condition, ...],
    target: int,
    penalty: float,
) -> tuple[_LogisticModel | None, _PlattCalibrator | None]:
    usable = tuple(item for item in conditions if target in item.examples_by_target)
    if len(usable) < 10:
        return None, None
    split = max(1, int(len(usable) * 0.8))
    if split >= len(usable):
        return None, None
    train_examples = _examples(usable[:split], target)
    calibration_examples = _examples(usable[split:], target)
    if not _both_classes(train_examples) or not _both_classes(calibration_examples):
        return None, None
    model = _fit_model(train_examples, penalty)
    if model is None:
        return None, None
    calibration_scores = tuple(model.score(example.features) for example in calibration_examples)
    calibrator = _fit_platt(
        calibration_scores,
        tuple(example.outcome_up for example in calibration_examples),
    )
    return model, calibrator


def _fit_model(examples: tuple[P23Example, ...], penalty: float) -> _LogisticModel | None:
    if not _both_classes(examples):
        return None
    rows = tuple(example.features for example in examples)
    labels = tuple(1.0 if example.outcome_up else 0.0 for example in examples)
    scaler = _fit_scaler(rows)
    if scaler is None:
        return None
    scaled = scaler.transform(rows)
    coefficients, intercept = _fit_l2(scaled, labels, penalty)
    if all(abs(item) <= NEAR_ZERO for item in coefficients):
        return None
    probabilities = tuple(
        _sigmoid(intercept + sum(c * x for c, x in zip(coefficients, row, strict=True)))
        for row in scaled
    )
    if len({round(item, 12) for item in probabilities}) <= 1:
        return None
    return _LogisticModel(coefficients, intercept, scaler)


def _fit_scaler(rows: tuple[tuple[float, ...], ...]) -> _Scaler | None:
    width = len(rows[0])
    means = []
    scales = []
    for index in range(width):
        values = [row[index] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        scale = math.sqrt(variance)
        if scale <= NEAR_ZERO:
            return None
        means.append(mean)
        scales.append(scale)
    return _Scaler(tuple(means), tuple(scales))


def _fit_l2(
    rows: tuple[tuple[float, ...], ...],
    labels: tuple[float, ...],
    penalty: float,
    *,
    iterations: int = 500,
    learning_rate: float = 0.05,
) -> tuple[tuple[float, ...], float]:
    positive_rate = min(0.99, max(0.01, sum(labels) / len(labels)))
    intercept = math.log(positive_rate / (1.0 - positive_rate))
    coefficients = [0.0] * len(rows[0])
    for _ in range(iterations):
        gradients = [0.0] * len(coefficients)
        intercept_gradient = 0.0
        for row, label in zip(rows, labels, strict=True):
            linear = intercept + sum(
                c * x for c, x in zip(coefficients, row, strict=True)
            )
            probability = _sigmoid(linear)
            error = probability - label
            intercept_gradient += error
            for index, value in enumerate(row):
                gradients[index] += error * value
        scale = 1.0 / len(rows)
        intercept -= learning_rate * intercept_gradient * scale
        for index, gradient in enumerate(gradients):
            coefficients[index] -= learning_rate * (
                gradient * scale + penalty * coefficients[index]
            )
    return tuple(coefficients), intercept


def _fit_platt(scores: tuple[float, ...], labels: tuple[bool, ...]) -> _PlattCalibrator | None:
    if len(scores) < 10 or len(set(labels)) <= 1:
        return None
    rows = tuple((score,) for score in scores)
    scaler = _fit_scaler(rows)
    if scaler is None:
        return None
    scaled_scores = tuple(row[0] for row in scaler.transform(rows))
    numeric_labels = tuple(1.0 if label else 0.0 for label in labels)
    coefficients, intercept = _fit_l2(
        tuple((score,) for score in scaled_scores),
        numeric_labels,
        0.01,
    )
    if abs(coefficients[0]) <= NEAR_ZERO:
        return None
    # Fold the one-dimensional scaler into the Platt coefficient for later raw-score use.
    coefficient = coefficients[0] / scaler.scales[0]
    adjusted_intercept = intercept - coefficients[0] * scaler.means[0] / scaler.scales[0]
    return _PlattCalibrator(coefficient, adjusted_intercept)


def _metrics(examples: tuple[P23Example, ...], probabilities: tuple[float, ...]) -> P23Metrics:
    labels = tuple(1.0 if example.outcome_up else 0.0 for example in examples)
    predictions = tuple(1.0 if probability >= 0.5 else 0.0 for probability in probabilities)
    brier = sum(
        (probability - label) ** 2
        for probability, label in zip(probabilities, labels, strict=True)
    ) / len(examples)
    log_loss = sum(
        _log_loss(probability, label)
        for probability, label in zip(probabilities, labels, strict=True)
    ) / len(examples)
    accuracy = sum(
        1
        for prediction, label in zip(predictions, labels, strict=True)
        if prediction == label
    ) / len(examples)
    pnls: list[float] = []
    evs: list[float] = []
    for example, probability in zip(examples, probabilities, strict=True):
        if example.executable_cost is None:
            continue
        selected_up = probability >= 0.5
        selected_probability = probability if selected_up else 1.0 - probability
        evs.append(selected_probability - example.executable_cost)
        won = example.outcome_up is selected_up
        pnls.append((1.0 - example.executable_cost) if won else -example.executable_cost)
    return P23Metrics(
        conditions=len(examples),
        up=sum(1 for example in examples if example.outcome_up),
        down=sum(1 for example in examples if not example.outcome_up),
        accuracy=accuracy,
        brier=brier,
        log_loss=log_loss,
        ece=_ece(probabilities, labels),
        mean_probability=sum(probabilities) / len(probabilities),
        empirical_up_rate=sum(labels) / len(labels),
        economic_coverage=len(evs) / len(examples),
        after_cost_ev=None if not evs else sum(evs) / len(evs),
        replay_pnl=None if not pnls else sum(pnls),
        maximum_drawdown=None if not pnls else _maximum_drawdown(pnls),
    )


def _bootstrap_uncertainty(
    examples: tuple[P23Example, ...],
    probabilities: tuple[float, ...],
) -> P23Uncertainty:
    economic_items = tuple(
        (example, probability)
        for example, probability in zip(examples, probabilities, strict=True)
        if example.executable_cost is not None
    )
    if len(economic_items) < 10:
        return P23Uncertainty(
            status="INSUFFICIENT_PRICING_COVERAGE",
            method="CONDITION_LEVEL_DETERMINISTIC_BOOTSTRAP",
            resamples=0,
            economic_condition_count=len(economic_items),
            after_cost_ev_p05=None,
            after_cost_ev_p50=None,
            after_cost_ev_p95=None,
            replay_pnl_p05=None,
            replay_pnl_p50=None,
            replay_pnl_p95=None,
        )
    seed_material = "|".join(item[0].condition_id for item in economic_items)
    state = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    ev_samples: list[float] = []
    pnl_samples: list[float] = []
    count = len(economic_items)
    for _ in range(BOOTSTRAP_RESAMPLES):
        evs: list[float] = []
        pnls: list[float] = []
        for _ in range(count):
            state = _lcg_next(state)
            example, probability = economic_items[state % count]
            cost = example.executable_cost
            if cost is None:
                continue
            selected_up = probability >= 0.5
            selected_probability = probability if selected_up else 1.0 - probability
            evs.append(selected_probability - cost)
            pnls.append((1.0 - cost) if example.outcome_up is selected_up else -cost)
        if evs:
            ev_samples.append(sum(evs) / len(evs))
            pnl_samples.append(sum(pnls))
    return P23Uncertainty(
        status="OK",
        method="CONDITION_LEVEL_DETERMINISTIC_BOOTSTRAP",
        resamples=len(ev_samples),
        economic_condition_count=count,
        after_cost_ev_p05=_percentile(ev_samples, 0.05),
        after_cost_ev_p50=_percentile(ev_samples, 0.50),
        after_cost_ev_p95=_percentile(ev_samples, 0.95),
        replay_pnl_p05=_percentile(pnl_samples, 0.05),
        replay_pnl_p50=_percentile(pnl_samples, 0.50),
        replay_pnl_p95=_percentile(pnl_samples, 0.95),
    )


def _lcg_next(state: int) -> int:
    return (6364136223846793005 * state + 1442695040888963407) % (2**64)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _failed_gates(
    final_challenger: P23Metrics,
    final_base: P23Metrics,
    folds: list[P23FoldResult],
) -> list[str]:
    failed = []
    if final_challenger.brier >= final_base.brier:
        failed.append("BRIER_NOT_BETTER_THAN_BASE_RATE")
    if final_challenger.log_loss >= final_base.log_loss:
        failed.append("LOGLOSS_NOT_BETTER_THAN_BASE_RATE")
    if final_challenger.ece > MAXIMUM_ACCEPTABLE_ECE:
        failed.append("CALIBRATION_NOT_ACCEPTABLE")
    if final_challenger.economic_coverage < MINIMUM_ECONOMIC_COVERAGE:
        failed.append("INSUFFICIENT_ECONOMIC_PRICING_COVERAGE")
    elif final_challenger.after_cost_ev is None or final_challenger.after_cost_ev <= 0:
        failed.append("OOS_AFTER_COST_EV_NOT_POSITIVE")
    selected_folds = [fold for fold in folds if fold.challenger.conditions > 0]
    if selected_folds:
        challenger_positive = [
            fold
            for fold in selected_folds
            if fold.challenger.log_loss < fold.base_rate.log_loss
            and fold.challenger.brier < fold.base_rate.brier
        ]
        if len(challenger_positive) < max(1, len(selected_folds) // 2):
            failed.append("WALK_FORWARD_NOT_STABLE")
    return failed


def _gate_table(
    final_challenger: P23Metrics,
    final_base: P23Metrics,
    folds: list[P23FoldResult],
    failed: list[str],
) -> list[dict[str, str]]:
    def gate(name: str, reason: str, evidence: str) -> dict[str, str]:
        return {
            "gate": name,
            "result": "FAIL" if reason in failed else "PASS",
            "evidence": evidence,
        }

    return [
        {
            "gate": "Data integrity",
            "result": "PASS",
            "evidence": "No accepted leakage/label conflict rows",
        },
        {
            "gate": "No leakage",
            "result": "PASS",
            "evidence": "Feature/source timestamps validated before observation",
        },
        {
            "gate": "Final sample adequate",
            "result": "PASS",
            "evidence": (
                f"{final_challenger.conditions} conditions, "
                f"UP={final_challenger.up}, DOWN={final_challenger.down}"
            ),
        },
        gate(
            "Brier beats base rate",
            "BRIER_NOT_BETTER_THAN_BASE_RATE",
            f"{final_challenger.brier:.6f} vs {final_base.brier:.6f}",
        ),
        gate(
            "LogLoss beats base rate",
            "LOGLOSS_NOT_BETTER_THAN_BASE_RATE",
            f"{final_challenger.log_loss:.6f} vs {final_base.log_loss:.6f}",
        ),
        gate(
            "Calibration acceptable",
            "CALIBRATION_NOT_ACCEPTABLE",
            f"ECE={final_challenger.ece:.6f}, max={MAXIMUM_ACCEPTABLE_ECE}",
        ),
        gate(
            "OOS after-cost EV > 0",
            "OOS_AFTER_COST_EV_NOT_POSITIVE",
            f"EV={final_challenger.after_cost_ev}",
        ),
        gate(
            "Economic evidence robust",
            "INSUFFICIENT_ECONOMIC_PRICING_COVERAGE",
            f"coverage={final_challenger.economic_coverage:.3f}",
        ),
        gate("Walk-forward stable", "WALK_FORWARD_NOT_STABLE", f"folds={len(folds)}"),
    ]


def _insufficient(dataset: P23Dataset, reason: str) -> P23Result:
    return P23Result(
        verdict="INSUFFICIENT_EVIDENCE",
        marker="P2_3_SOL_5M_INSUFFICIENT_EVIDENCE",
        dataset=dataset,
        selected_checkpoint=None,
        selected_regularization=None,
        final_challenger_spec={},
        development_folds=(),
        final_challenger=None,
        final_base_rate=None,
        gate_table=[{"gate": "Evidence", "result": "FAIL", "evidence": reason}],
        failed_gates=(reason,),
        notes=("NO_GOVERNANCE_OR_EXECUTION_CHANGE",),
        coefficient_summary={},
        uncertainty=None,
        final_evaluation_count=0,
    )


def _no_go(dataset: P23Dataset, reasons: tuple[str, ...]) -> P23Result:
    return P23Result(
        verdict="NO_GO",
        marker="P2_3_SOL_5M_NO_GO",
        dataset=dataset,
        selected_checkpoint=None,
        selected_regularization=None,
        final_challenger_spec={},
        development_folds=(),
        final_challenger=None,
        final_base_rate=None,
        gate_table=[{"gate": reason, "result": "FAIL", "evidence": reason} for reason in reasons],
        failed_gates=reasons,
        notes=("NO_GOVERNANCE_OR_EXECUTION_CHANGE",),
        coefficient_summary={},
        uncertainty=None,
        final_evaluation_count=0,
    )


def _examples(conditions: tuple[P23Condition, ...], target: int) -> tuple[P23Example, ...]:
    return tuple(
        condition.examples_by_target[target]
        for condition in conditions
        if target in condition.examples_by_target
    )


def _both_classes(examples: tuple[P23Example, ...]) -> bool:
    return any(item.outcome_up for item in examples) and any(
        not item.outcome_up for item in examples
    )


def _base_rate(examples: tuple[P23Example, ...]) -> float:
    return min(0.99, max(0.01, sum(1 for item in examples if item.outcome_up) / len(examples)))


def _sigmoid(value: float) -> float:
    clipped = max(-40.0, min(40.0, value))
    return 1.0 / (1.0 + math.exp(-clipped))


def _log_loss(probability: float, label: float) -> float:
    clipped = min(1.0 - 1e-12, max(1e-12, probability))
    return -(label * math.log(clipped) + (1.0 - label) * math.log(1.0 - clipped))


def _ece(probabilities: tuple[float, ...], labels: tuple[float, ...]) -> float:
    total = len(probabilities)
    result = 0.0
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = [
            (probability, label)
            for probability, label in zip(probabilities, labels, strict=True)
            if lower <= probability < upper or (index == 9 and probability == 1.0)
        ]
        if members:
            confidence = sum(item[0] for item in members) / len(members)
            observed = sum(item[1] for item in members) / len(members)
            result += len(members) / total * abs(confidence - observed)
    return result


def _maximum_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _coefficient_signs(coefficients: tuple[float, ...]) -> dict[str, str]:
    signs = {}
    for name, coefficient in zip(FEATURE_NAMES, coefficients, strict=True):
        if abs(coefficient) <= NEAR_ZERO:
            signs[name] = "near_zero"
        elif coefficient > 0:
            signs[name] = "positive"
        else:
            signs[name] = "negative"
    return signs


def _coefficient_summary(
    model: _LogisticModel,
    folds: list[P23FoldResult],
) -> dict[str, object]:
    fold_signs = {name: {"positive": 0, "negative": 0, "near_zero": 0} for name in FEATURE_NAMES}
    for fold in folds:
        for name, sign in fold.coefficient_signs.items():
            fold_signs[name][sign] += 1
    return {
        "intercept": model.intercept,
        "coefficients": {
            name: coefficient
            for name, coefficient in zip(FEATURE_NAMES, model.coefficients, strict=True)
        },
        "fold_sign_stability": fold_signs,
    }


def _parse_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _dataset_dict(dataset: P23Dataset) -> dict[str, object]:
    return {
        "code_sha": dataset.code_sha,
        "fingerprint": dataset.fingerprint,
        "asset": TARGET_ASSET.value,
        "horizon": TARGET_HORIZON.value,
        "unique_conditions": dataset.unique_conditions,
        "rows": dataset.row_count,
        "up": dataset.up_count,
        "down": dataset.down_count,
        "checkpoint_counts": dataset.checkpoint_counts,
        "earliest": dataset.earliest,
        "latest": dataset.latest,
        "excluded_rows": dataset.excluded_rows,
        "leakage_violations": dataset.leakage_violations,
        "label_conflicts": dataset.label_conflicts,
    }


def _metrics_row(name: str, metrics: P23Metrics) -> str:
    ev = metrics.after_cost_ev
    pnl = metrics.replay_pnl
    return (
        f"| {name} | {metrics.conditions} | {metrics.accuracy:.6f} | "
        f"{metrics.brier:.6f} | {metrics.log_loss:.6f} | {metrics.ece:.6f} | "
        f"{metrics.economic_coverage:.6f} | {'NA' if ev is None else f'{ev:.6f}'} | "
        f"{'NA' if pnl is None else f'{pnl:.6f}'} |"
    )


def _next_action(verdict: str) -> str:
    if verdict == "GO":
        return "SOL-5M SHADOW CANDIDATE OBSERVATION. Do not enable PAPER."
    if verdict == "NO_GO":
        return "STOP SOL-5M CURRENT HYPOTHESIS. Wait for future data before a new hypothesis."
    return "Collect future SOL-5m conditions and checkpoint-time executable pricing evidence."
