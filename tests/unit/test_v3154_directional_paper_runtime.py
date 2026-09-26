import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, FeatureValue, FeatureVector, Horizon
from direction_engine_v3.market_data import MarketBucket
from direction_engine_v3.models import (
    PAPER_RESEARCH_BASELINE_CALIBRATION_VERSION,
    PAPER_RESEARCH_BASELINE_MODEL_VERSION,
    load_paper_registry_from_corpus,
    paper_research_baseline_forecast,
)
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_directional_corpus_requires_feature_vector_and_official_outcome(tmp_path) -> None:
    repository = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repository.initialize()
    repository.save_pre_outcome(
        record_id="placeholder",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition",
        observed_at=NOW,
        payload={"model_state": "TRAINING_CORPUS_REQUIRED"},
    )
    repository.attach_verified_outcome_once(
        record_id="placeholder",
        outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": True},
        attached_at=NOW + timedelta(minutes=5),
    )
    repository.save_pre_outcome(
        record_id="proxy-label",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition",
        observed_at=NOW,
        payload=_training_payload(),
    )
    repository.attach_verified_outcome_once(
        record_id="proxy-label",
        outcome={"settlement_source_kind": "PROXY", "outcome_up": True},
        attached_at=NOW + timedelta(minutes=5),
    )
    repository.save_pre_outcome(
        record_id="official-label",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition",
        observed_at=NOW,
        payload=_training_payload(),
    )
    repository.attach_verified_outcome_once(
        record_id="official-label",
        outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": True},
        attached_at=NOW + timedelta(minutes=5),
    )

    records = repository.training_ready_records(
        asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES
    )

    assert [record.record_id for record in records] == ["official-label"]
    assert records[0].outcome_up is True


def test_directional_corpus_training_ready_query_is_indexed_and_boundable(tmp_path) -> None:
    repository = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repository.initialize()
    for index in range(3):
        repository.save_pre_outcome(
            record_id=f"official-label-{index}",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            condition_id=f"condition-{index}",
            observed_at=NOW + timedelta(seconds=index),
            payload=_training_payload(),
        )
        repository.attach_verified_outcome_once(
            record_id=f"official-label-{index}",
            outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": index % 2 == 0},
            attached_at=NOW + timedelta(minutes=5),
        )

    records = repository.training_ready_records(
        asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES, limit=2
    )
    with sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(directional_corpus)").fetchall()
        }

    assert [record.record_id for record in records] == [
        "official-label-0",
        "official-label-1",
    ]
    assert "idx_directional_corpus_training_ready" in indexes
    assert "idx_directional_corpus_condition_observed" in indexes


def test_paper_registry_reports_exact_bucket_insufficient_sample_without_fallback(
    tmp_path,
) -> None:
    repository = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repository.initialize()
    repository.save_pre_outcome(
        record_id="btc-5m",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition",
        observed_at=NOW,
        payload=_training_payload(),
    )
    repository.attach_verified_outcome_once(
        record_id="btc-5m",
        outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": True},
        attached_at=NOW + timedelta(minutes=5),
    )

    result = load_paper_registry_from_corpus(repository, minimum_samples=2)
    btc = result.report_for(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    eth = result.report_for(MarketBucket(Asset.ETH, Horizon.FIVE_MINUTES))

    assert btc.current_count == 1
    assert btc.state == "INSUFFICIENT_SAMPLE"
    assert eth.current_count == 0
    assert eth.state == "TRAINING_CORPUS_REQUIRED"
    btc_state = result.registry.state_for(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    eth_state = result.registry.state_for(MarketBucket(Asset.ETH, Horizon.FIVE_MINUTES))
    assert btc_state.readiness.ready is False
    assert eth_state.readiness.ready is False


def test_paper_registry_loader_rejects_too_small_startup_bound(tmp_path) -> None:
    repository = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repository.initialize()

    with pytest.raises(ValueError, match="max_records_per_bucket"):
        load_paper_registry_from_corpus(
            repository,
            minimum_samples=2,
            max_records_per_bucket=1,
        )


def test_paper_research_baseline_is_deterministic_external_only_and_unpromotable() -> None:
    features = _feature_vector()

    first, readiness = paper_research_baseline_forecast(
        features, generated_at=NOW + timedelta(seconds=1)
    )
    second, second_readiness = paper_research_baseline_forecast(
        features, generated_at=NOW + timedelta(seconds=1)
    )

    assert first == second
    assert readiness == second_readiness
    assert first.model_version == PAPER_RESEARCH_BASELINE_MODEL_VERSION
    assert first.calibration_version == PAPER_RESEARCH_BASELINE_CALIBRATION_VERSION
    assert readiness.ready is True
    assert readiness.sample_count == 0


def test_paper_research_baseline_rejects_polymarket_alpha_feature() -> None:
    features = FeatureVector(
        "market",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        "feature-set",
        (
            FeatureValue(
                "polymarket_contract_price",
                Decimal("0.50"),
                "CLOB",
                NOW,
            ),
        ),
        NOW,
    )

    with pytest.raises(ValueError, match="Polymarket price alpha"):
        paper_research_baseline_forecast(features, generated_at=NOW)


def _training_payload() -> dict[str, object]:
    return {
        "feature_vector": {
            "market_id": "market",
            "asset": "BTC",
            "horizon": "5m",
            "feature_set_version": "feature-set",
            "generated_at": NOW.isoformat(),
            "features": [
                {
                    "name": "ptb_normalized_distance",
                    "value": "0.01",
                    "source": "BINANCE_EXTERNAL_FEATURES",
                    "source_ts": NOW.isoformat(),
                }
            ],
        },
        "price_to_beat": {
            "condition_id": "condition",
            "persistence_id": "ptb-id",
            "value": "60000",
        },
    }


def _feature_vector() -> FeatureVector:
    names = {
        "ptb_normalized_distance": Decimal("0.01"),
        "tte_fraction": Decimal("0.5"),
        "short_return": Decimal("0.01"),
        "medium_return": Decimal("0.01"),
        "momentum": Decimal("0.01"),
        "realized_volatility": Decimal("0.001"),
        "volatility_acceleration": Decimal("0"),
        "spot_perp_basis": Decimal("0"),
        "external_book_imbalance": Decimal("0.20"),
        "microprice_distance": Decimal("0.01"),
        "trade_imbalance": Decimal("0.20"),
        "signal_stability": Decimal("0.90"),
        "flip_rate": Decimal("0.05"),
        "regime_score": Decimal("0.20"),
    }
    return FeatureVector(
        "market",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        "feature-set",
        tuple(
            FeatureValue(name, value, "BINANCE_EXTERNAL_FEATURES", NOW)
            for name, value in names.items()
        ),
        NOW,
    )
