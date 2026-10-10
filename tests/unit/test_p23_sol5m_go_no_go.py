import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import Asset, Horizon, OrderSide
from direction_engine_v3.evaluation.sol5m_go_no_go import (
    FEATURE_NAMES,
    P2_3_SOL5M_ACCEPTED_CODE_SHA,
    P2_3_SOL5M_PROSPECTIVE_CUTOFF,
    P2_3_SOL5M_PROSPECTIVE_DATASET_FINGERPRINT,
    P2_3R_SOL5M_ARTIFACT_SCHEMA_VERSION,
    P2_3R_SOL5M_MODEL_ID,
    load_sol5m_dataset,
    load_sol5m_research_artifact,
    materialize_sol5m_frozen_challenger,
    render_markdown_report,
    run_sol5m_go_no_go,
)
from direction_engine_v3.evaluation.sol5m_prospective import (
    run_sol5m_prospective_evaluation,
)
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    PolymarketBook,
    PolymarketLevel,
)
from direction_engine_v3.pricing import LiquidityRole, PricingPolicy, simulate_depth
from direction_engine_v3.shadow.daemon import _prospective_pricing_diagnostics
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository
from direction_engine_v3.storage.directional_corpus import (
    SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
)

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
    assert result.research_artifact is not None
    assert result.research_artifact["artifact_schema_version"] == (
        P2_3R_SOL5M_ARTIFACT_SCHEMA_VERSION
    )
    assert result.research_artifact["execution_permission"] == "NONE"
    assert "RESEARCH_ONLY" in report


def test_p23r_research_artifact_is_reproducible_and_loadable(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(corpus, index=index, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)

    first = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )
    second = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )
    assert first.research_artifact is not None
    assert second.research_artifact is not None
    assert first.research_artifact["artifact_checksum"] == second.research_artifact[
        "artifact_checksum"
    ]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(first.research_artifact), encoding="utf-8")

    challenger = load_sol5m_research_artifact(path)

    assert challenger.ready is True
    assert challenger.model_id == P2_3R_SOL5M_MODEL_ID
    assert challenger.artifact_checksum == first.research_artifact["artifact_checksum"]
    assert challenger.checkpoint is not None
    checkpoint = challenger.checkpoint
    feature_row = next(
        condition.examples_by_target[checkpoint]
        for condition in load_sol5m_dataset(tmp_path / "corpus.sqlite3", code_sha="sha").conditions
        if checkpoint in condition.examples_by_target
    ).features
    prediction = challenger.predict(feature_row)
    assert prediction["status"] == "READY"
    assert prediction["artifact_checksum"] == challenger.artifact_checksum


def test_p23r_loader_rejects_summary_only_or_tampered_artifact(tmp_path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text('{"marker":"P2_3_SOL_5M_INSUFFICIENT_EVIDENCE"}', encoding="utf-8")

    summary_challenger = load_sol5m_research_artifact(summary_path)

    assert summary_challenger.ready is False
    assert summary_challenger.reason.startswith("P2_3R_RESEARCH_ARTIFACT_INVALID")

    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(corpus, index=index, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)
    result = run_sol5m_go_no_go(
        tmp_path / "corpus.sqlite3",
        code_sha="sha",
        minimum_final_conditions=8,
    )
    assert result.research_artifact is not None
    tampered = dict(result.research_artifact)
    tampered["feature_names"] = list(reversed(FEATURE_NAMES))
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")

    tampered_challenger = load_sol5m_research_artifact(tampered_path)

    assert tampered_challenger.ready is False
    assert "FEATURE_ORDER_MISMATCH" in tampered_challenger.reason


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


def test_p23_frozen_challenger_fails_closed_on_fingerprint_mismatch(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    for index in range(120):
        _write_condition(corpus, index=index, asset=Asset.SOL, horizon=Horizon.FIVE_MINUTES)

    challenger = materialize_sol5m_frozen_challenger(
        tmp_path / "corpus.sqlite3",
        code_sha=P2_3_SOL5M_ACCEPTED_CODE_SHA,
        cutoff_observed_at=NOW + timedelta(days=1),
    )

    assert challenger.ready is False
    assert challenger.reason == "P2_3_DATASET_FINGERPRINT_MISMATCH"
    assert challenger.spec["expected_p2_3_dataset_fingerprint"] == (
        P2_3_SOL5M_PROSPECTIVE_DATASET_FINGERPRINT
    )
    assert challenger.spec["execution_permission"] == "NONE"


def test_sol5m_prospective_evidence_is_future_only_idempotent_and_labeled(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    observed_at = P2_3_SOL5M_PROSPECTIVE_CUTOFF + timedelta(minutes=5)
    corpus.save_sol5m_prospective_evidence(
        evidence_id="evidence-1",
        condition_id="sol5m-future-1",
        market_id="market-sol5m-future-1",
        checkpoint_target_tte_seconds=45,
        evidence_schema_version=SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        feature_schema_version="v3.15.3-directional-official-ptb",
        model_id="P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY",
        model_artifact_checksum="checksum",
        observed_at=observed_at,
        actual_tte_seconds=45,
        predicted_side="UP",
        raw_model_probability=0.52,
        calibrated_probability=0.54,
        selected_probability=0.54,
        selected_side_executable_cost=0.42,
        up_executable_cost=0.42,
        down_executable_cost=0.61,
        pricing_status="CHECKPOINT_EXECUTABLE_PRICING_READY",
        timing_status="CHECKPOINT_45S_WITHIN_TOLERANCE",
        payload={"model_id": "P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY"},
    )
    corpus.save_sol5m_prospective_evidence(
        evidence_id="evidence-duplicate",
        condition_id="sol5m-future-1",
        market_id="market-sol5m-future-1",
        checkpoint_target_tte_seconds=45,
        evidence_schema_version=SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        feature_schema_version="v3.15.3-directional-official-ptb",
        model_id="P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY",
        model_artifact_checksum="checksum",
        observed_at=observed_at,
        actual_tte_seconds=45,
        predicted_side="UP",
        raw_model_probability=0.52,
        calibrated_probability=0.54,
        selected_probability=0.54,
        selected_side_executable_cost=0.42,
        up_executable_cost=0.42,
        down_executable_cost=0.61,
        pricing_status="CHECKPOINT_EXECUTABLE_PRICING_READY",
        timing_status="CHECKPOINT_45S_WITHIN_TOLERANCE",
        payload={"model_id": "P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY"},
    )
    attached = corpus.attach_verified_outcome_to_condition_once(
        condition_id="sol5m-future-1",
        outcome={
            "settlement_source_kind": "OFFICIAL",
            "settlement_source": "POLYMARKET_OFFICIAL_METADATA",
            "outcome_up": True,
            "winning_side": "UP",
            "official_resolved_at": (observed_at + timedelta(minutes=5)).isoformat(),
            "evidence_hash": "proof",
        },
        official_resolved_at=observed_at + timedelta(minutes=5),
        attached_at=observed_at + timedelta(minutes=6),
    )

    summary = corpus.sol5m_prospective_evidence_summary()
    samples = corpus.recent_sol5m_prospective_evidence(limit=5)
    assert attached == 1
    assert summary["total_rows"] == 1
    assert summary["labeled_unique_conditions"] == 1
    assert summary["pricing_coverage"] == "1.0"
    assert summary["prediction_valid_rows"] == 1
    assert summary["pricing_valid_rows"] == 1
    assert summary["economic_eligible_rows"] == 1
    assert samples[0]["official_outcome"] == "UP"
    assert samples[0]["prediction_status"] == "VALID"
    assert samples[0]["economic_eligible"] is True


def test_sol5m_prospective_prediction_unavailable_does_not_mask_pricing(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    observed_at = P2_3_SOL5M_PROSPECTIVE_CUTOFF + timedelta(minutes=5)

    corpus.save_sol5m_prospective_evidence(
        evidence_id="evidence-1",
        condition_id="sol5m-future-1",
        market_id="market-sol5m-future-1",
        checkpoint_target_tte_seconds=45,
        evidence_schema_version=SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        feature_schema_version="v3.15.3-directional-official-ptb",
        model_id="P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY",
        model_artifact_checksum=None,
        observed_at=observed_at,
        actual_tte_seconds=45,
        predicted_side=None,
        raw_model_probability=None,
        calibrated_probability=None,
        selected_probability=None,
        selected_side_executable_cost=None,
        up_executable_cost=0.42,
        down_executable_cost=0.61,
        pricing_status="CHECKPOINT_EXECUTABLE_PRICING_READY",
        prediction_status="FROZEN_CHALLENGER_UNAVAILABLE",
        prediction_failure_reason="P2_3_DATASET_FINGERPRINT_MISMATCH",
        pricing_failure_reason=None,
        label_status="UNLABELED",
        economic_eligible=False,
        valid_capture_start=observed_at,
        timing_status="CHECKPOINT_45S_WITHIN_TOLERANCE",
        payload={
            "model_id": "P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY",
            "artifact_checksum": None,
            "prediction": {
                "status": "FROZEN_CHALLENGER_UNAVAILABLE",
                "reason": "P2_3_DATASET_FINGERPRINT_MISMATCH",
            },
            "up_executable_cost": 0.42,
            "down_executable_cost": 0.61,
        },
    )

    summary = corpus.sol5m_prospective_evidence_summary()
    samples = corpus.recent_sol5m_prospective_evidence(limit=5)

    assert summary["prediction_valid_rows"] == 0
    assert summary["pricing_valid_rows"] == 1
    assert summary["economic_eligible_rows"] == 0
    assert summary["prediction_failure_reason_counts"] == {
        "P2_3_DATASET_FINGERPRINT_MISMATCH": 1
    }
    assert samples[0]["prediction_status"] == "FROZEN_CHALLENGER_UNAVAILABLE"
    assert samples[0]["pricing_status"] == "CHECKPOINT_EXECUTABLE_PRICING_READY"
    assert samples[0]["up_executable_cost"] == 0.42
    assert samples[0]["down_executable_cost"] == 0.61


def test_sol5m_prospective_samples_include_pricing_diagnostics(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    observed_at = P2_3_SOL5M_PROSPECTIVE_CUTOFF + timedelta(minutes=5)

    corpus.save_sol5m_prospective_evidence(
        evidence_id="evidence-1",
        condition_id="sol5m-future-1",
        market_id="market-sol5m-future-1",
        checkpoint_target_tte_seconds=45,
        evidence_schema_version=SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        feature_schema_version="v3.15.3-directional-official-ptb",
        model_id="P2_3R_SOL5M_45S_RESEARCH_ONLY",
        model_artifact_checksum="checksum",
        observed_at=observed_at,
        actual_tte_seconds=45,
        predicted_side="UP",
        raw_model_probability=0.52,
        calibrated_probability=0.54,
        selected_probability=0.54,
        selected_side_executable_cost=None,
        up_executable_cost=None,
        down_executable_cost=0.61,
        pricing_status="ONE_SIDE_PRICING_MISSING",
        pricing_failure_reason="ONE_SIDE_EXECUTABLE_COST_MISSING",
        timing_status="CHECKPOINT_45S_WITHIN_TOLERANCE",
        payload={
            "model_id": "P2_3R_SOL5M_45S_RESEARCH_ONLY",
            "pricing_diagnostics": {
                "requested_quantity": "1",
                "up": {"reason": "NO_ASK_DEPTH", "ask_level_count": 0},
                "down": {"reason": "READY", "ask_level_count": 1},
            },
        },
    )

    samples = corpus.recent_sol5m_prospective_evidence(limit=5)

    assert samples[0]["pricing_diagnostics"] == {
        "requested_quantity": "1",
        "up": {"reason": "NO_ASK_DEPTH", "ask_level_count": 0},
        "down": {"reason": "READY", "ask_level_count": 1},
    }


def test_sol5m_pricing_diagnostics_explain_one_side_missing() -> None:
    observed_at = NOW
    up_book = _book("condition-1", "up-token", observed_at, asks=())
    down_book = _book(
        "condition-1",
        "down-token",
        observed_at,
        asks=(PolymarketLevel(Decimal("0.40"), Decimal("2")),),
    )
    up_fee = _fee("condition-1", observed_at)
    down_fee = _fee("condition-1", observed_at)
    down_pricing = simulate_depth(
        down_book,
        down_fee,
        side=OrderSide.BUY,
        requested_quantity=Decimal("1"),
        limit_price=Decimal("1"),
        role=LiquidityRole.TAKER,
        observed_at=observed_at,
        policy=PricingPolicy(
            max_book_age=timedelta(seconds=30),
            max_fee_age=timedelta(seconds=30),
            slippage_buffer_bps=Decimal("10"),
            fee_buffer_bps=Decimal("500"),
        ),
    )

    diagnostics = _prospective_pricing_diagnostics(
        observed_at=observed_at,
        up_book=up_book,
        down_book=down_book,
        up_fee=up_fee,
        down_fee=down_fee,
        up_pricing=None,
        down_pricing=down_pricing,
        directional_pricing_status="EXECUTABLE_PRICE_READY",
    )

    assert diagnostics["requested_quantity"] == "1"
    assert diagnostics["up"]["reason"] == "NO_ASK_DEPTH"
    assert diagnostics["up"]["ask_level_count"] == 0
    assert diagnostics["down"]["reason"] == "READY"
    assert diagnostics["down"]["token_id"] == "down-token"
    assert diagnostics["down"]["fill_fraction"] == "1"


def test_sol5m_prospective_evaluator_collects_without_fabricating_go_no_go(tmp_path) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    corpus.initialize()
    observed_at = P2_3_SOL5M_PROSPECTIVE_CUTOFF + timedelta(minutes=5)
    corpus.save_sol5m_prospective_evidence(
        evidence_id="evidence-1",
        condition_id="sol5m-future-1",
        market_id="market-sol5m-future-1",
        checkpoint_target_tte_seconds=45,
        evidence_schema_version=SOL5M_PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        feature_schema_version="v3.15.3-directional-official-ptb",
        model_id="P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY",
        model_artifact_checksum="checksum",
        observed_at=observed_at,
        actual_tte_seconds=45,
        predicted_side="UP",
        raw_model_probability=0.52,
        calibrated_probability=0.54,
        selected_probability=0.54,
        selected_side_executable_cost=None,
        up_executable_cost=None,
        down_executable_cost=None,
        pricing_status="SELECTED_SIDE_PRICING_MISSING",
        timing_status="CHECKPOINT_45S_WITHIN_TOLERANCE",
        payload={"model_id": "P2_3_SOL5M_45S_FROZEN_RESEARCH_ONLY"},
    )

    result = run_sol5m_prospective_evaluation(tmp_path / "corpus.sqlite3")

    assert result.marker == "SOL5M_PROSPECTIVE_EVIDENCE_COLLECTING"
    assert result.brier is None
    assert "COLLECTING_MINIMUM_LABELED_CONDITIONS" in result.failed_gates


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


def _lineage(source: DataSource, observed_at: datetime) -> EventLineage:
    return EventLineage(
        source=source,
        source_ts=observed_at,
        recv_ts=observed_at,
        normalized_ts=observed_at,
        recv_monotonic_ns=1,
    )


def _book(
    condition_id: str,
    token_id: str,
    observed_at: datetime,
    *,
    asks: tuple[PolymarketLevel, ...],
) -> PolymarketBook:
    return PolymarketBook(
        condition_id=condition_id,
        token_id=token_id,
        bids=(PolymarketLevel(Decimal("0.10"), Decimal("1")),),
        asks=asks,
        checksum=f"checksum-{token_id}",
        minimum_order_size=Decimal("1"),
        tick_size=Decimal("0.01"),
        lineage=_lineage(DataSource.POLYMARKET_CLOB, observed_at),
    )


def _fee(condition_id: str, observed_at: datetime) -> FeeSchedule:
    return FeeSchedule(
        condition_id=condition_id,
        maker_base_bps=Decimal("0"),
        taker_base_bps=Decimal("0"),
        rate=Decimal("0"),
        exponent=Decimal("0"),
        lineage=_lineage(DataSource.POLYMARKET_CLOB, observed_at),
    )
