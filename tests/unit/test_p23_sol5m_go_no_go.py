from datetime import UTC, datetime, timedelta

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.evaluation.sol5m_go_no_go import (
    FEATURE_NAMES,
    load_sol5m_dataset,
    render_markdown_report,
    run_sol5m_go_no_go,
)
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def test_p23_loads_only_sol_5m_official_current_schema_and_freezes_features(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    _write_condition(corpus, index=0, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)
    _write_condition(corpus, index=1, asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES)
    _write_condition(
        corpus,
        index=2,
        asset=Asset.SOL,
        horizon=Horizon.FIVE_MINUTES,
        feature_schema_version="old-schema",
    )

    dataset = load_sol5m_dataset(tmp_path / "corpus.sqlite3", code_sha="sha")

    assert dataset.unique_conditions == 1
    assert dataset.row_count == 4
    assert dataset.excluded_rows["FEATURE_SCHEMA_MISMATCH"] == 4
    assert "spot_perp_basis" not in FEATURE_NAMES
    assert "signal_stability" not in FEATURE_NAMES
    assert "flip_rate" in FEATURE_NAMES
    assert dataset.fingerprint


def test_p23_runs_single_bucket_offline_without_economic_fabrication(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(80):
        _write_condition(corpus, index=index, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)

    result = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )
    report = render_markdown_report(result)

    assert result.selected_checkpoint in {120, 90, 60, 45}
    assert result.final_challenger is not None
    assert result.final_challenger.economic_coverage == 0
    assert result.marker == "P2_3_SOL_5M_INSUFFICIENT_EVIDENCE"
    assert "INSUFFICIENT_ECONOMIC_PRICING_COVERAGE" in result.failed_gates
    assert result.final_challenger_spec["execution_permission"] == "NONE"
    assert "RESEARCH_ONLY" in report


def test_p23_freezes_policy_before_one_final_holdout_evaluation(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(
            corpus,
            index=index,
            asset=Asset.SOL,
            horizon=Horizon.FIVE_MINUTES,
            executable_cost=0.45,
        )

    result = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )

    assert result.final_evaluation_count == 1
    assert result.final_challenger_spec["frozen_before_final_holdout"] is True
    assert result.final_challenger_spec["selection_source"] == "development_walk_forward_only"
    frozen_policy = result.final_challenger_spec["frozen_policy"]
    assert isinstance(frozen_policy, dict)
    assert frozen_policy["policy_status"] == "FROZEN_BEFORE_FINAL_HOLDOUT"
    assert frozen_policy["replay_rule"] == "calibrated_probability >= 0.5 selects UP else DOWN"
    assert frozen_policy["final_holdout_unique_conditions"] == result.final_challenger.conditions


def test_p23_pricing_replay_uses_persisted_checkpoint_costs(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(
            corpus,
            index=index,
            asset=Asset.SOL,
            horizon=Horizon.FIVE_MINUTES,
            executable_cost=0.20 if index % 2 == 0 else 0.80,
        )

    result = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )

    assert result.final_challenger is not None
    assert result.final_challenger.economic_coverage == 1
    assert result.final_challenger.after_cost_ev is not None
    assert result.final_challenger.replay_pnl is not None
    assert result.final_challenger.maximum_drawdown is not None
    assert result.uncertainty is not None
    assert result.uncertainty.status == "OK"
    assert result.uncertainty.economic_condition_count == result.final_challenger.conditions


def test_p23_uncertainty_reports_insufficient_pricing_without_fabrication(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(corpus, index=index, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)

    result = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )

    assert result.uncertainty is not None
    assert result.uncertainty.status == "INSUFFICIENT_PRICING_COVERAGE"
    assert result.uncertainty.after_cost_ev_p50 is None
    assert result.final_challenger is not None
    assert result.final_challenger.after_cost_ev is None


def test_p23_rejects_leakage_before_training(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    _write_condition(
        corpus,
        index=0,
        asset=Asset.SOL,
        horizon=Horizon.FIVE_MINUTES,
        future_source_ts=True,
    )

    dataset = load_sol5m_dataset(tmp_path / "corpus.sqlite3", code_sha="sha")

    assert dataset.unique_conditions == 0
    assert dataset.leakage_violations == 4
    assert dataset.excluded_rows["FUTURE_SOURCE_TS:ptb_normalized_distance"] == 4


def _write_condition(
    corpus: SQLiteDirectionalCorpusRepository,
    *,
    index: int,
    asset: Asset,
    horizon: Horizon,
    feature_schema_version: str = "v3.15.3-directional-official-ptb",
    future_source_ts: bool = False,
    executable_cost: float | None = None,
) -> None:
    condition_id = f"{asset.value}-{horizon.value}-{index}"
    observed_base = NOW + timedelta(minutes=index)
    outcome_up = index % 2 == 0
    for target in (120, 90, 60, 45):
        observed_at = observed_base + timedelta(milliseconds=target)
        source_ts = observed_at + timedelta(seconds=1) if future_source_ts else observed_at
        corpus.save_checkpoint_observation(
            checkpoint_id=f"{condition_id}-{target}",
            asset=asset,
            horizon=horizon,
            condition_id=condition_id,
            checkpoint_target_tte_seconds=target,
            feature_schema_version=feature_schema_version,
            observed_at=observed_at,
            actual_tte_seconds=target,
            payload={
                "market_id": f"market-{condition_id}",
                "window_start": (observed_at - timedelta(minutes=5)).isoformat(),
                "window_end": (observed_at + timedelta(seconds=target)).isoformat(),
                **(
                    {}
                    if executable_cost is None
                    else {"selected_side_executable_cost": executable_cost}
                ),
                "feature_vector": {
                    "feature_set_version": feature_schema_version,
                    "generated_at": observed_at.isoformat(),
                    "features": [
                        {
                            "name": name,
                            "value": value,
                            "source_ts": source_ts.isoformat(),
                        }
                        for name, value in _features(index, target, outcome_up).items()
                    ],
                },
            },
        )
    official_resolved_at = observed_base + timedelta(minutes=10)
    corpus.attach_verified_outcome_to_condition_once(
        condition_id=condition_id,
        outcome={
            "settlement_source_kind": "OFFICIAL",
            "settlement_source": "POLYMARKET_OFFICIAL_METADATA",
            "outcome_up": outcome_up,
            "winning_side": "UP" if outcome_up else "DOWN",
            "official_resolved_at": official_resolved_at.isoformat(),
            "evidence_hash": f"hash-{condition_id}",
        },
        official_resolved_at=official_resolved_at,
        attached_at=official_resolved_at + timedelta(minutes=1),
    )


def _features(index: int, target: int, outcome_up: bool) -> dict[str, str]:
    direction = 1 if outcome_up else -1
    drift = index / 10000
    target_jitter = target / 100000
    signal = direction * 0.1 + drift + target_jitter
    return {
        "ptb_normalized_distance": str(signal),
        "tte_fraction": str(target / 300 + drift),
        "short_return": str(signal * 0.5),
        "medium_return": str(signal * 0.4 + 0.001),
        "momentum": str(signal * 0.3 + 0.002),
        "realized_volatility": str(abs(signal) + 0.01 + drift),
        "volatility_acceleration": str(direction * 0.02 + drift),
        "trade_imbalance": str(signal * 2),
        "external_book_imbalance": str(signal * 1.5),
        "microprice_distance": str(signal * 0.25),
        "flip_rate": str(0.40 - direction * 0.10 + drift),
        "signal_stability": str(0.60 + direction * 0.10 - drift),
        "regime_score": str(signal * 0.8),
        "spot_perp_basis": "0",
    }
