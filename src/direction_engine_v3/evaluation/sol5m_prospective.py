"""SOL-5m prospective economic evidence evaluation.

This module reads only the forward-looking prospective evidence table.  It
does not retrain, recalibrate, promote a model, or enable PAPER/LIVE execution.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from direction_engine_v3.storage.directional_corpus import (
    SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    SOL5M_PROSPECTIVE_MINIMUM_LABELED_CONDITIONS,
    SOL5M_PROSPECTIVE_MINIMUM_PRICING_COVERAGE,
)


@dataclass(frozen=True, slots=True)
class Sol5mProspectiveResult:
    marker: str
    condition_count: int
    labeled_unique_conditions: int
    priced_labeled_conditions: int
    pricing_coverage: float
    brier: float | None
    log_loss: float | None
    ece: float | None
    after_cost_ev: float | None
    replay_pnl: float | None
    maximum_drawdown: float | None
    failed_gates: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "marker": self.marker,
            "schema_version": SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
            "condition_count": self.condition_count,
            "labeled_unique_conditions": self.labeled_unique_conditions,
            "priced_labeled_conditions": self.priced_labeled_conditions,
            "pricing_coverage": self.pricing_coverage,
            "brier": self.brier,
            "log_loss": self.log_loss,
            "ece": self.ece,
            "after_cost_ev": self.after_cost_ev,
            "replay_pnl": self.replay_pnl,
            "maximum_drawdown": self.maximum_drawdown,
            "failed_gates": list(self.failed_gates),
            "training_started": False,
            "model_promotion_changed": False,
            "paper_execution_permission_changed": False,
            "real_order_submission": False,
        }


def run_sol5m_prospective_evaluation(db_path: Path) -> Sol5mProspectiveResult:
    rows = _prospective_rows(db_path)
    labeled = [row for row in rows if row["outcome_up"] is not None]
    unique_labeled = {str(row["condition_id"]) for row in labeled}
    priced = [
        row
        for row in labeled
        if row["selected_side_executable_cost"] is not None
        and row["calibrated_probability"] is not None
    ]
    pricing_coverage = len(priced) / len(labeled) if labeled else 0.0
    failed: list[str] = []
    if len(unique_labeled) < SOL5M_PROSPECTIVE_MINIMUM_LABELED_CONDITIONS:
        failed.append("COLLECTING_MINIMUM_LABELED_CONDITIONS")
    if pricing_coverage < SOL5M_PROSPECTIVE_MINIMUM_PRICING_COVERAGE:
        failed.append("INSUFFICIENT_PRICING_COVERAGE")
    if failed:
        marker = (
            "SOL5M_PROSPECTIVE_PRICING_BLOCKED"
            if failed == ["INSUFFICIENT_PRICING_COVERAGE"]
            else "SOL5M_PROSPECTIVE_EVIDENCE_COLLECTING"
        )
        return Sol5mProspectiveResult(
            marker=marker,
            condition_count=len({str(row["condition_id"]) for row in rows}),
            labeled_unique_conditions=len(unique_labeled),
            priced_labeled_conditions=len(priced),
            pricing_coverage=pricing_coverage,
            brier=None,
            log_loss=None,
            ece=None,
            after_cost_ev=None,
            replay_pnl=None,
            maximum_drawdown=None,
            failed_gates=tuple(failed),
        )
    probabilities = [_as_float(row["calibrated_probability"]) for row in labeled]
    labels = [1.0 if row["outcome_up"] else 0.0 for row in labeled]
    pnls: list[float] = []
    evs: list[float] = []
    for row in priced:
        probability = _as_float(row["calibrated_probability"])
        predicted_side = str(row["predicted_side"])
        cost = _as_float(row["selected_side_executable_cost"])
        selected_up = predicted_side == "UP"
        selected_probability = probability if selected_up else 1.0 - probability
        evs.append(selected_probability - cost)
        won = bool(row["outcome_up"]) is selected_up
        pnls.append((1.0 - cost) if won else -cost)
    return Sol5mProspectiveResult(
        marker="SOL5M_PROSPECTIVE_EVIDENCE_READY_FOR_GO_NO_GO",
        condition_count=len({str(row["condition_id"]) for row in rows}),
        labeled_unique_conditions=len(unique_labeled),
        priced_labeled_conditions=len(priced),
        pricing_coverage=pricing_coverage,
        brier=sum((p - y) ** 2 for p, y in zip(probabilities, labels, strict=True))
        / len(labels),
        log_loss=sum(_log_loss(p, y) for p, y in zip(probabilities, labels, strict=True))
        / len(labels),
        ece=_ece(probabilities, labels),
        after_cost_ev=sum(evs) / len(evs),
        replay_pnl=sum(pnls),
        maximum_drawdown=_maximum_drawdown(pnls),
        failed_gates=(),
    )


def _prospective_rows(db_path: Path) -> list[dict[str, object]]:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT condition_id,predicted_side,calibrated_probability,
                       selected_side_executable_cost,outcome_json
                FROM sol5m_prospective_evidence
                WHERE evidence_schema_version=?
                ORDER BY observed_at ASC, condition_id ASC
                """,
                (SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    result: list[dict[str, object]] = []
    for row in rows:
        outcome = _json_object(row["outcome_json"])
        outcome_up = outcome.get("outcome_up")
        result.append(
            {
                "condition_id": row["condition_id"],
                "predicted_side": row["predicted_side"],
                "calibrated_probability": row["calibrated_probability"],
                "selected_side_executable_cost": row["selected_side_executable_cost"],
                "outcome_up": outcome_up if isinstance(outcome_up, bool) else None,
            }
        )
    return result


def _json_object(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError("expected finite numeric value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("expected finite numeric value")
    return result


def _log_loss(probability: float, label: float) -> float:
    clipped = min(1.0 - 1e-12, max(1e-12, probability))
    return -(label * math.log(clipped) + (1.0 - label) * math.log(1.0 - clipped))


def _ece(probabilities: list[float], labels: list[float]) -> float:
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
