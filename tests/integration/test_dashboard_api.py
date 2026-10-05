import asyncio
import time
from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer

from direction_engine_v3.app import build_dashboard_snapshot, create_app
from direction_engine_v3.app import server as dashboard_server
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository


def test_dashboard_snapshot_preserves_scope_and_live_defaults() -> None:
    snapshot = build_dashboard_snapshot(now=datetime(2026, 1, 1, tzinfo=UTC)).as_dict()

    assert snapshot["scope"] == {
        "assets": ["BTC", "ETH", "SOL", "XRP"],
        "horizons": ["5m", "15m", "1h"],
    }
    assert snapshot["mode"] == {
        "app_mode": "PAPER",
        "live_trading_enabled": False,
        "live_auto_arm": False,
    }
    assert snapshot["execution"]["real_order_submission"] is False
    assert snapshot["readiness"]["ready"] is False


def test_dashboard_app_exposes_only_get_read_only_routes() -> None:
    app = create_app()
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}

    assert routes == {
        ("GET", "/"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/health/shadow-ready"),
        ("GET", "/health/trading-ready"),
        ("GET", "/metrics"),
        ("GET", "/api/dashboard"),
        ("GET", "/api/paper/summary"),
        ("GET", "/api/paper/reconciliation"),
        ("GET", "/api/paper/performance"),
        ("GET", "/api/paper/trades"),
        ("GET", "/api/paper/trades/{id}"),
        ("GET", "/api/paper/abstains"),
        ("GET", "/api/shadow/status"),
        ("GET", "/api/directional/status"),
        ("GET", "/api/directional/audit"),
        ("GET", "/api/directional/corpus/labels"),
        ("GET", "/api/directional/corpus/readiness"),
        ("GET", "/api/model/governance"),
        ("GET", "/api/feature/integrity"),
    }
    assert all(method == "GET" for method, _path in routes)


def test_dashboard_root_serves_existing_read_only_html() -> None:
    asyncio.run(_assert_dashboard_root_serves_existing_read_only_html())


def test_dashboard_root_survives_blocked_api_builder(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "API_RESPONSE_TIMEOUT_SECONDS", 0.1)

    def blocked_summary() -> dict[str, object]:
        time.sleep(0.5)
        return {"status": "TOO_LATE"}

    monkeypatch.setattr(dashboard_server, "build_paper_summary", blocked_summary)

    asyncio.run(_assert_dashboard_root_survives_blocked_api_builder())


async def _assert_dashboard_root_serves_existing_read_only_html() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/")
        body = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    assert response.content_type == "text/html"
    assert "Direction Engine V3" in body
    assert "PAPER / SHADOW" in body
    assert "Trade Decision Breakdown" in body
    assert "/api/directional/audit?hours=24" in body
    assert "Exposure Reconciliation" in body
    assert "/api/paper/reconciliation" in body
    assert "NO REAL ORDER" in body
    assert "APP_MODE=PAPER" in body
    assert "LIVE_TRADING_ENABLED=false" in body
    assert "real_order_submission=false" in body
    assert "Last updated" in body
    assert "Command Center" in body
    assert "PAPER ledger, risk and runtime status at a glance" in body
    assert "Active PAPER Positions" in body
    assert "Latest Settlements" in body
    assert "Risk / Cooldown" in body
    assert "PAPER Trades" in body
    assert "Open Exposure" in body
    assert "Open Trades" in body
    assert "Realized PnL" in body
    assert "Available Capital" in body
    assert "PAPER Trade Health" in body
    assert "TRADE_FLOW_ACTIVE" in body
    assert "RISK_BLOCKED" in body
    assert "Settlement / Data Health" in body
    assert "12 Bucket Live Status / Performance" in body
    assert "Abstains / Rejections" in body
    assert (
        "Model Governance / Corpus Readiness / Feature Integrity / Structural Arb / "
        "Data / Read-only API Status"
        in body
    )
    assert "P2.2B Corpus Readiness" in body
    assert "/api/directional/corpus/readiness" in body
    assert "/api/directional/corpus/labels?limit=20" in body
    assert "P2.1 Feature Integrity" in body
    assert "/api/feature/integrity" in body
    assert "Raw JSON" in body
    assert "<pre id=\"overview\"" not in body
    assert "Loading..." not in body
    assert "AbortController" in body
    assert "timeout" in body
    assert "state.refreshing" in body
    assert "lightEndpoints" in body
    assert "heavyEndpoints" in body
    assert "data-load-heavy" in body
    assert "loadHeavy" in body
    assert "Object.keys(lightEndpoints)" in body
    assert 'url.startsWith("/health/")' in body
    assert "j.not_ready=true" in body
    assert "render();refresh();setInterval(refresh,5000)" in body
    assert 'method:"POST"' not in body
    assert 'method: "POST"' not in body
    assert "submit_order" not in body
    assert "create_order" not in body
    assert "wallet" not in body.lower()
    assert "signing" not in body.lower()
    assert "/api/paper/summary" in body
    assert "/api/paper/performance?strategy=DIRECTIONAL_EDGE" in body
    assert "/api/paper/trades?strategy=DIRECTIONAL_EDGE&limit=25" in body
    assert "/api/paper/abstains?limit=25" in body
    assert "/api/directional/status" in body
    assert "/api/model/governance" in body
    assert "/api/directional/corpus/readiness" in body
    assert "/api/shadow/status" in body
    assert "/api/dashboard" in body

    snapshot = build_dashboard_snapshot().as_dict()
    assert snapshot["mode"] == {
        "app_mode": "PAPER",
        "live_trading_enabled": False,
        "live_auto_arm": False,
    }
    assert snapshot["execution"]["real_order_submission"] is False


async def _assert_dashboard_root_survives_blocked_api_builder() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        blocked_task = asyncio.create_task(client.get("/api/paper/summary"))
        await asyncio.sleep(0.02)
        root_response = await client.get("/")
        root_body = await root_response.text()
        blocked_response = await blocked_task
        blocked_payload = await blocked_response.json()
    finally:
        await client.close()

    assert root_response.status == 200
    assert "Direction Engine V3" in root_body
    assert blocked_response.status == 200
    assert blocked_payload == {
        "status": "API_TIMEOUT",
        "reason": "dashboard read-only data builder exceeded timeout",
        "real_order_submission": False,
    }


def test_dashboard_api_builder_exception_returns_payload_with_http_200(monkeypatch) -> None:
    def broken_shadow_status() -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard_server, "build_shadow_status", broken_shadow_status)

    asyncio.run(_assert_builder_exception_returns_payload_with_http_200())


def test_feature_integrity_endpoint_is_read_only_and_reports_insufficient_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))

    asyncio.run(_assert_feature_integrity_endpoint_reports_insufficient_evidence())


async def _assert_feature_integrity_endpoint_reports_insufficient_evidence() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/feature/integrity")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert payload["real_order_submission"] is False
    assert payload["status"] == "P2_1_FEATURE_INTEGRITY_AUDIT_READY"
    assert payload["acceptance_marker"] == "P2_1_EVIDENCE_INSUFFICIENT"
    assert payload["root_cause_classification"] == "INSUFFICIENT_EVIDENCE"
    assert payload["feature_schema_transition"] == {
        "production_schema": "v3.15.3-directional-official-ptb",
        "diagnostic_comparator_version": "P2.1_TEMPORAL_COMPARATOR_V1",
        "conditional_p2_1b_schema": (
            "v3.15.3-directional-official-ptb-source-dedup-v2"
        ),
        "historical_rows_rewritten": False,
        "production_checkpoint_rows_write_dedup_v2": False,
        "mixed_schema_training_policy": "FEATURE_SCHEMA_MISMATCH",
    }
    assert payload["scope"]["production_execution_changed"] is False
    assert payload["scope"]["model_promotion_changed"] is False


def test_feature_integrity_endpoint_reports_bounded_duplicate_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    for index, bucket_key in enumerate(("BTC-5m", "BTC-15m", "BTC-1h")):
        repository.append_event(
            event_id=f"pipeline-{index}",
            window_id="window",
            event_type="MARKET_DATA_PIPELINE",
            bucket_key=bucket_key,
            payload={
                "feature_status": "FEATURES_READY",
                "feature_diagnostics": {
                    "scope": "asset_level",
                    "dedup_policy": "none_legacy_append_all",
                },
                "feature_integrity_comparison": {
                    "version": "P2.1_TEMPORAL_COMPARATOR_V1",
                    "production_semantics": "legacy_append_all",
                    "diagnostic_semantics": "source_identity_dedup",
                    "diagnostic_feature_status": "FEATURES_READY",
                    "diagnostic_diagnostics": {
                        "scope": "asset_level",
                        "dedup_policy": "source_identity_v1",
                        "reference_sample_count": 3,
                        "book_sample_count": 1,
                        "trade_sample_count": 2,
                        "dedup_reference_skip_count": 1,
                        "dedup_book_skip_count": 1,
                        "dedup_trade_skip_count": 1,
                        "reference_identity_conflict_count": 0,
                        "book_identity_conflict_count": 1 if index == 0 else 0,
                        "unique_reference_source_identity_count": 3,
                        "unique_book_source_identity_count": 1,
                        "unique_trade_source_identity_count": 2,
                        "near_duplicate_reference_count": 1,
                        "near_duplicate_book_count": 0,
                        "near_duplicate_trade_count": 0,
                    },
                    "feature_deltas": {
                        "status": "FEATURE_COMPARISON_READY",
                        "features": {
                            "short_return": {
                                "legacy": "0.01",
                                "diagnostic": "0.02",
                                "absolute_delta": "0.01",
                            }
                        },
                    },
                },
            },
            observed_at=observed_at + timedelta(seconds=index),
        )
        repository.append_event(
            event_id=f"strategy-{index}",
            window_id="window",
            event_type="STRATEGY_EVALUATION",
            bucket_key=bucket_key,
            payload={
                "strategy": "DIRECTIONAL_EDGE",
                "feature_integrity_comparison": {
                    "decision_impact": {
                        "evaluated": True,
                        "same_decision": index != 0,
                        "legacy_action": "ABSTAIN",
                        "diagnostic_action": "TRADE" if index == 0 else "ABSTAIN",
                        "legacy_reason": "SIGNAL_UNSTABLE",
                        "diagnostic_reason": "EDGE_AVAILABLE"
                        if index == 0
                        else "SIGNAL_UNSTABLE",
                        "signal_unstable_changed": index == 0,
                        "flip_rate_too_high_changed": False,
                        "abstain_to_trade_candidate": index == 0,
                        "trade_candidate_to_abstain": False,
                        "counterfactual_trades_executed": False,
                    },
                },
            },
            observed_at=observed_at + timedelta(seconds=index),
        )

    asyncio.run(_assert_feature_integrity_endpoint_reports_duplicate_evidence())


def test_directional_corpus_endpoints_are_bounded_read_only_and_training_safe(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "directional_corpus.sqlite3")
    corpus.initialize()
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    for index, target in enumerate((120, 90, 60, 45)):
        corpus.save_checkpoint_observation(
            checkpoint_id=f"checkpoint-{target}",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            condition_id="condition-btc-1",
            checkpoint_target_tte_seconds=target,
            feature_schema_version="v3.15.3-directional-official-ptb",
            observed_at=observed_at + timedelta(seconds=index),
            actual_tte_seconds=target,
            payload={
                "market_id": "market-btc-1",
                "window_start": (observed_at - timedelta(minutes=5)).isoformat(),
                "window_end": (observed_at - timedelta(minutes=1)).isoformat(),
                "feature_vector": {
                    "feature_set_version": "v3.15.3-directional-official-ptb",
                    "generated_at": observed_at.isoformat(),
                    "features": [
                        {
                            "name": "short_return",
                            "value": str(index + 1),
                            "source_ts": observed_at.isoformat(),
                        },
                        {
                            "name": "spot_perp_basis",
                            "value": "0",
                            "source_ts": observed_at.isoformat(),
                        },
                    ],
                },
                "price_to_beat": {"persistence_id": "ptb-1", "value": "1"},
            },
        )
    corpus.attach_verified_outcome_to_condition_once(
        condition_id="condition-btc-1",
        outcome={
            "settlement_source_kind": "OFFICIAL",
            "settlement_source": "POLYMARKET_OFFICIAL_METADATA",
            "outcome_up": True,
            "winning_side": "UP",
            "official_resolved_at": (observed_at + timedelta(minutes=1)).isoformat(),
            "evidence_hash": "official-hash",
        },
        official_resolved_at=observed_at + timedelta(minutes=1),
        attached_at=observed_at + timedelta(minutes=2),
    )
    paper.save_corpus_label_task(
        condition_id="condition-btc-1",
        evidence_hash="official-hash",
        state="LABELED",
        attempted_at=observed_at + timedelta(minutes=2),
        next_attempt_at=None,
        reason="LABELED",
        payload={
            "asset": "BTC",
            "horizon": "5m",
            "market_id": "market-btc-1",
            "status": "SETTLEMENT_READY",
            "rows_attached": 4,
        },
    )

    asyncio.run(_assert_directional_corpus_endpoints())


async def _assert_feature_integrity_endpoint_reports_duplicate_evidence() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/feature/integrity")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    temporal = payload["temporal_integrity"]
    assert temporal["source_identity_policy"] == "diagnostic_shadow_comparator_only"
    assert temporal["production_temporal_semantics"] == "legacy_append_all"
    assert temporal["total_source_identity_dedup_skips"] == 9
    assert temporal["total_source_identity_conflicts"] == 1
    assert temporal["decision_impact_source_status"] == "STRATEGY_EVALUATION_READY"
    assert temporal["decision_impact_bucket_count"] == 3
    assert temporal["duplicate_rates"]["trade"] == {
        "duplicate_count": 3,
        "retained_count": 6,
        "previous_total_count": 9,
        "rate": "0.3333333333333333333333333333",
    }
    assert (
        temporal["cross_horizon_reingestion"]["assets"]["BTC"][
            "dedup_rejected_reingestions"
        ]["trade"]
        == 3
    )
    assert payload["feature_delta_summary"]["status"] == (
        "COMPARATOR_FEATURE_DELTA_READY"
    )
    assert payload["feature_delta_summary"]["features"]["short_return"]["count"] == 3
    assert payload["decision_impact_summary"]["counterfactual_trades_executed"] is False
    assert payload["decision_impact_summary"]["evaluations_compared"] == 3
    assert payload["decision_impact_summary"]["different_decision_count"] == 1
    assert payload["decision_impact_summary"]["abstain_to_trade_candidate_count"] == 1


async def _assert_directional_corpus_endpoints() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        labels_response = await client.get(
            "/api/directional/corpus/labels?limit=500&asset=BTC&horizon=5m"
        )
        labels_payload = await labels_response.json()
        readiness_response = await client.get("/api/directional/corpus/readiness")
        readiness_payload = await readiness_response.json()
    finally:
        await client.close()

    assert labels_response.status == 200
    assert labels_payload["status"] == "DIRECTIONAL_CORPUS_LABELS_READY"
    assert labels_payload["real_order_submission"] is False
    assert labels_payload["limit"] == 100
    assert labels_payload["hard_max_limit"] == 100
    assert labels_payload["label_count"] == 1
    label = labels_payload["labels"][0]
    assert label["condition_id"] == "condition-btc-1"
    assert label["market_id"] == "market-btc-1"
    assert label["official_outcome"] == "UP"
    assert label["resolution_source"] == "POLYMARKET_OFFICIAL_METADATA"
    assert label["evidence_hash"] == "official-hash"
    assert label["checkpoint_row_count"] == 4
    assert label["dataset_schema_version"] == "directional_checkpoint_observations:v1"

    assert readiness_response.status == 200
    assert readiness_payload["real_order_submission"] is False
    assert readiness_payload["training_started"] is False
    assert readiness_payload["model_promotion_changed"] is False
    assert readiness_payload["paper_execution_permission_changed"] is False
    assert readiness_payload["labeler"]["state_counts"] == {"LABELED": 1}
    assert readiness_payload["labeler"]["batch_policy"] == {
        "default_max_conditions_per_pass": 1,
        "hard_max_conditions_per_pass": 10,
        "changed_in_p2_2b": False,
    }
    buckets = readiness_payload["corpus"]["buckets"]
    assert len(buckets) == 12
    btc = next(row for row in buckets if row["asset"] == "BTC" and row["horizon"] == "5m")
    assert btc["unique_conditions"] == 1
    assert btc["labeled_unique_conditions"] == 1
    assert btc["up_labeled_conditions"] == 1
    assert btc["down_labeled_conditions"] == 0
    assert btc["eligible_unique_conditions"] == 1
    assert btc["label_coverage_eligible_conditions"] == "1.0"
    assert btc["placeholder_zero_features"] == ["spot_perp_basis"]
    assert btc["future_timestamp_violations"] == 0
    assert btc["official_label_conflict_count"] == 0
    assert btc["variance_failed_features"] == []
    assert btc["failing_features"] == []
    assert "INSUFFICIENT_UNIQUE_CONDITIONS" in btc["all_failed_gates"]
    assert btc["readiness_state"] == "INSUFFICIENT_UNIQUE_CONDITIONS"


def test_p2_2c_readiness_variance_policy_excludes_placeholders_and_names_failures(
    tmp_path,
) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "directional_corpus.sqlite3")
    corpus.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    _write_labeled_checkpoint_conditions(
        corpus,
        observed_at=observed_at,
        condition_count=100,
        features_for_index=lambda index: {
            "short_return": "0.000000001" if index % 2 else "0.000000002",
            "spot_perp_basis": "0",
        },
    )

    report = corpus.training_readiness_report(now=observed_at + timedelta(hours=1))
    assert report["policy_version"] == "DIRECTIONAL_CORPUS_READINESS_V2"
    btc = _bucket(report, Asset.BTC, Horizon.FIVE_MINUTES)
    assert btc["readiness_state"] == "TRAINING_READY"
    assert btc["primary_reason"] == "TRAINING_READY"
    assert btc["all_failed_gates"] == []
    assert btc["placeholder_zero_features"] == ["spot_perp_basis"]
    assert "spot_perp_basis" in btc["zero_variance_features"]
    assert btc["variance_failed_features"] == []
    assert btc["failing_features"] == []
    assert btc["feature_statistics"]["short_return"]["all_rows"]["unique"] == 2
    assert btc["feature_statistics"]["short_return"]["all_rows"]["stddev"] is not None
    assert (
        btc["feature_classifications"]["spot_perp_basis"]
        == "PLACEHOLDER_NON_SIGNAL"
    )

    failing = SQLiteDirectionalCorpusRepository(
        tmp_path / "directional_corpus_required_constant.sqlite3"
    )
    failing.initialize()
    _write_labeled_checkpoint_conditions(
        failing,
        observed_at=observed_at,
        condition_count=100,
        features_for_index=lambda _index: {
            "short_return": "1",
            "spot_perp_basis": "0",
        },
    )
    failing_report = failing.training_readiness_report(
        now=observed_at + timedelta(hours=1)
    )
    failing_btc = _bucket(failing_report, Asset.BTC, Horizon.FIVE_MINUTES)
    assert failing_btc["readiness_state"] == "FEATURE_VARIANCE_FAILED"
    assert failing_btc["primary_reason"] == "FEATURE_VARIANCE_FAILED"
    assert failing_btc["variance_failed_features"] == ["short_return"]
    assert failing_btc["failing_features"][0]["name"] == "short_return"
    assert failing_btc["failing_features"][0]["classification"] == (
        "REQUIRED_SIGNAL_FEATURE"
    )
    assert "FEATURE_VARIANCE_FAILED" in failing_btc["all_failed_gates"]


def test_p2_2c_readiness_reason_precedence_uses_sample_before_variance(
    tmp_path,
) -> None:
    corpus = SQLiteDirectionalCorpusRepository(tmp_path / "directional_corpus.sqlite3")
    corpus.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    _write_labeled_checkpoint_conditions(
        corpus,
        observed_at=observed_at,
        condition_count=7,
        asset=Asset.XRP,
        horizon=Horizon.ONE_HOUR,
        features_for_index=lambda _index: {
            "short_return": "1",
            "spot_perp_basis": "0",
        },
    )

    report = corpus.training_readiness_report(now=observed_at + timedelta(hours=2))
    xrp = _bucket(report, Asset.XRP, Horizon.ONE_HOUR)
    assert xrp["readiness_state"] == "INSUFFICIENT_UNIQUE_CONDITIONS"
    assert xrp["primary_reason"] == "INSUFFICIENT_UNIQUE_CONDITIONS"
    assert "FEATURE_VARIANCE_FAILED" in xrp["all_failed_gates"]
    assert xrp["variance_failed_features"] == ["short_return"]


def _write_labeled_checkpoint_conditions(
    corpus: SQLiteDirectionalCorpusRepository,
    *,
    observed_at: datetime,
    condition_count: int,
    features_for_index,
    asset: Asset = Asset.BTC,
    horizon: Horizon = Horizon.FIVE_MINUTES,
) -> None:
    for index in range(condition_count):
        condition_id = f"{asset.value}-{horizon.value}-condition-{index}"
        label_up = index % 2 == 0
        for target in (120, 90, 60, 45):
            corpus.save_checkpoint_observation(
                checkpoint_id=f"{condition_id}-{target}",
                asset=asset,
                horizon=horizon,
                condition_id=condition_id,
                checkpoint_target_tte_seconds=target,
                feature_schema_version="v3.15.3-directional-official-ptb",
                observed_at=observed_at + timedelta(seconds=index, milliseconds=target),
                actual_tte_seconds=target,
                payload={
                    "market_id": f"market-{condition_id}",
                    "window_start": (observed_at - timedelta(minutes=10)).isoformat(),
                    "window_end": (observed_at - timedelta(minutes=1)).isoformat(),
                    "feature_vector": {
                        "feature_set_version": "v3.15.3-directional-official-ptb",
                        "generated_at": observed_at.isoformat(),
                        "features": [
                            {
                                "name": name,
                                "value": value,
                                "source_ts": observed_at.isoformat(),
                            }
                            for name, value in features_for_index(index).items()
                        ],
                    },
                },
            )
        official_resolved_at = observed_at + timedelta(hours=1)
        corpus.attach_verified_outcome_to_condition_once(
            condition_id=condition_id,
            outcome={
                "settlement_source_kind": "OFFICIAL",
                "settlement_source": "POLYMARKET_OFFICIAL_METADATA",
                "outcome_up": label_up,
                "winning_side": "UP" if label_up else "DOWN",
                "official_resolved_at": official_resolved_at.isoformat(),
                "evidence_hash": f"hash-{condition_id}",
            },
            official_resolved_at=official_resolved_at,
            attached_at=official_resolved_at + timedelta(minutes=1),
        )


def _bucket(report: dict[str, object], asset: Asset, horizon: Horizon) -> dict[str, object]:
    buckets = report["buckets"]
    assert isinstance(buckets, list)
    bucket = next(
        row
        for row in buckets
        if isinstance(row, dict)
        and row["asset"] == asset.value
        and row["horizon"] == horizon.value
    )
    return bucket


async def _assert_builder_exception_returns_payload_with_http_200() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/shadow/status")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert payload == {
        "status": "API_UNAVAILABLE",
        "reason": "RuntimeError",
        "real_order_submission": False,
    }


def test_directional_status_api_is_read_only_and_paper_labeled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()

    asyncio.run(_assert_directional_status_api_is_read_only_and_paper_labeled())


async def _assert_directional_status_api_is_read_only_and_paper_labeled() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert payload["label"] == "PAPER / SHADOW — NO REAL ORDER"
    assert payload["real_order_submission"] is False
    assert len(payload["buckets"]) == 12
    assert {item["asset"] for item in payload["buckets"]} == {"BTC", "ETH", "SOL", "XRP"}
    assert not any(item["last_paper_trade"] for item in payload["buckets"])
    assert {item["label"] for item in payload["buckets"]} == {
        "PAPER / SHADOW — NO REAL ORDER"
    }


def test_directional_status_missing_runtime_is_explicit_and_side_effect_free(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))

    asyncio.run(_assert_directional_status_missing_runtime_is_explicit(tmp_path))


async def _assert_directional_status_missing_runtime_is_explicit(tmp_path) -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert payload["label"] == "PAPER / SHADOW — NO REAL ORDER"
    assert payload["status"] == "DATABASE_NOT_INITIALIZED"
    assert payload["real_order_submission"] is False
    assert payload["buckets"] == []
    assert not (tmp_path / "shadow_evidence.sqlite3").exists()
    assert not (tmp_path / "paper.sqlite3").exists()
    assert list(tmp_path.iterdir()) == []


def test_directional_status_api_exposes_official_proxy_ptb_health(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    repository.append_event(
        event_id="event",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "official_status": "OFFICIAL_REFERENCE_READY",
            "proxy_status": "PROXY_READY",
            "ptb_status": "PTB_READY",
            "ptb_reason": "PTB_ESTABLISHED",
            "chainlink": {
                "selected_topic": "crypto_prices_twap_sixty",
                "subscription_status": "SUBSCRIBED",
                "message_count": 3,
                "parse_success_count": 3,
                "parse_failure_count": 0,
            },
            "binance_hourly": {
                "last_http_status": "OK",
                "last_match_status": "MATCH",
            },
            "action": "ABSTAIN",
            "reason": "MODEL_UNAVAILABLE",
            "corpus_sample_count": 0,
        },
        observed_at=observed_at,
    )

    asyncio.run(_assert_directional_status_contains_feed_health())


def test_directional_status_prefers_fresh_pipeline_failure_over_old_strategy_row(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    observed_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    repository.append_event(
        event_id="strategy-old",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "MODEL_UNAVAILABLE",
        },
        observed_at=observed_at,
    )
    repository.append_event(
        event_id="pipeline-new",
        window_id="window",
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        payload={
            "latest_observed_at": "2026-09-15T12:01:00+00:00",
            "discovery_status": "READY",
            "fee_status": "FAILED",
            "fee_error": "MARKET_DATA_SCHEMA_ERROR:FEE_PARSE",
            "ptb_status": "PTB_READY",
            "ptb_reason": "PTB_ESTABLISHED",
            "pipeline_stages": [{"stage": "FEE_PARSE", "status": "FAIL"}],
        },
        observed_at=datetime(2026, 9, 15, 12, 1, tzinfo=UTC),
    )

    asyncio.run(_assert_pipeline_failure_is_visible())


async def _assert_pipeline_failure_is_visible() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["latest_observed_at"] == "2026-09-15T12:01:00+00:00"
    assert bucket["fee_status"] == "FAILED"
    assert bucket["fee_error"] == "MARKET_DATA_SCHEMA_ERROR:FEE_PARSE"
    assert bucket["ptb_status"] == "PTB_READY"


def test_directional_status_preserves_newest_pipeline_event_and_schema_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="pipeline-old",
        window_id="window",
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        payload={
            "latest_observed_at": "2026-09-15T12:00:00+00:00",
            "fee_status": "FAILED",
            "fee_error": "OLD_SCHEMA",
            "ptb_status": "PTB_UNAVAILABLE",
            "chainlink": {
                "per_asset": {
                    "BTC": {
                        "parse_success_count": 0,
                        "history_size": 0,
                    }
                }
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    repository.append_event(
        event_id="pipeline-new",
        window_id="window",
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        payload={
            "latest_observed_at": "2026-09-15T12:02:00+00:00",
            "fee_status": "READY",
            "ptb_status": "PTB_READY",
            "chainlink": {
                "per_asset": {
                    "BTC": {
                        "parse_success_count": 4,
                        "history_size": 4,
                        "subscription_snapshot_count": 1,
                        "last_frame_class": "LIVE_UPDATE",
                    }
                }
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 2, tzinfo=UTC),
    )

    asyncio.run(_assert_newest_pipeline_event_is_visible())


def test_directional_status_preserves_newest_strategy_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="strategy-old",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "OLD_REASON",
            "model_state": "OLD_MODEL_STATE",
        },
        observed_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    repository.append_event(
        event_id="strategy-new",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "MODEL_UNAVAILABLE",
            "model_state": "TRAINING_CORPUS_REQUIRED",
            "model_version": "PAPER_RESEARCH_BASELINE",
            "calibration_version": "PAPER_RESEARCH_BASELINE_UNPROMOTABLE",
            "p_up": "0.62",
            "p_down": "0.38",
            "selected_side": "UP",
            "executable_cost": "0.40",
            "net_edge": "0.21",
            "directional_execution": {"risk_approved": True},
        },
        observed_at=datetime(2026, 9, 15, 12, 2, tzinfo=UTC),
    )

    asyncio.run(_assert_newest_strategy_event_is_visible())


def test_directional_status_exposes_decision_audit_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="strategy-audit",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "SIGNAL_UNSTABLE",
            "p_up": "0.60",
            "p_down": "0.40",
            "pricing_status": "EXECUTABLE_PRICE_READY",
            "decision_audit": {
                "version": "DIRECTIONAL_DECISION_AUDIT_V1",
                "final_decision": "ABSTAIN",
                "final_reason": "SIGNAL_UNSTABLE",
                "first_failing_gate": "SIGNAL_STABILITY_GATE",
                "signal": {
                    "signal_stability_actual": "0.60",
                    "signal_stability_minimum": "0.70",
                    "flip_rate_actual": "0.40",
                    "flip_rate_maximum": "0.20",
                },
                "pricing": {
                    "counterfactual_pricing_available": True,
                    "counterfactual_net_edge": "0.04",
                },
                "edge": {"counterfactual_edge_margin": "0.01"},
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 2, tzinfo=UTC),
    )

    asyncio.run(_assert_directional_status_contains_decision_audit())


def test_directional_audit_endpoint_uses_real_time_window_and_coverage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    now = datetime.now(UTC)
    repository.append_event(
        event_id="old",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={"strategy": "DIRECTIONAL_EDGE", "action": "ABSTAIN", "reason": "OLD"},
        observed_at=now - timedelta(hours=25),
    )
    repository.append_event(
        event_id="legacy",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "UNKNOWN_NEW_REASON",
            },
        observed_at=now - timedelta(hours=1),
    )
    repository.append_event(
        event_id="audit",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "ABSTAIN",
            "reason": "SIGNAL_UNSTABLE",
            "decision_audit": {
                "version": "DIRECTIONAL_DECISION_AUDIT_V1",
                "first_failing_gate": "SIGNAL_STABILITY_GATE",
                "signal": {
                    "signal_stability_actual": "0.60",
                    "flip_rate_actual": "0.40",
                    "signal_stability_minus_minimum": "-0.10",
                    "maximum_flip_rate_minus_actual": "-0.20",
                },
                "edge": {"counterfactual_edge_margin": "0.02"},
                },
            },
        observed_at=now - timedelta(minutes=30),
    )

    asyncio.run(_assert_directional_audit_endpoint())


def test_directional_status_does_not_show_risk_reject_as_open_position(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="strategy-risk-reject",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "TRADE",
            "reason": "TRADE",
            "directional_execution": {
                "router_status": "ROUTED",
                "risk_approved": False,
                "risk_reasons": ["CONSECUTIVE_LOSS_COOLDOWN_ACTIVE"],
                "current_losing_streak": 27,
                "cooldown_active": True,
                "cooldown_until": "2026-09-15T13:00:00+00:00",
                "cooldown_remaining_seconds": 1800,
                "last_successful_settlement_at": "2026-09-15T12:00:00+00:00",
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 30, tzinfo=UTC),
    )

    asyncio.run(_assert_risk_reject_is_not_open_position())


def test_directional_status_exposes_paper_risk_brake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="strategy-risk-brake",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "TRADE",
            "reason": "TRADE",
            "directional_execution": {
                "router_status": "PAPER_DRAWDOWN_BRAKE_ACTIVE",
                "risk_approved": False,
                "risk_reasons": [
                    "PAPER_DRAWDOWN_BRAKE_ACTIVE",
                    "OPEN_EXPOSURE_LIMIT",
                ],
                "risk_brake_active": True,
                "risk_brake_reason": "PAPER_DRAWDOWN_BRAKE_ACTIVE",
                "paper_current_equity": "30.00",
                "open_cost_basis": "12.00",
                "directional_open_cost_basis": "12.00",
                "global_open_cost_basis": "20.52",
                "structural_open_cost_basis": "8.52",
                "directional_available_capital": "18.00",
                "open_trade_count": 4,
                "directional_open_trade_count": 4,
                "global_open_trade_count": 6,
                "structural_open_trade_count": 2,
                "same_asset_open_count": 1,
                "recent_directional_win_rate": "0.10",
                "recent_directional_pnl": "-6.00",
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 30, tzinfo=UTC),
    )

    asyncio.run(_assert_paper_risk_brake_is_visible())


def test_directional_status_shows_open_only_after_paper_fill(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    repository = SQLiteShadowRepository(tmp_path / "shadow_evidence.sqlite3")
    repository.initialize()
    repository.append_event(
        event_id="strategy-filled",
        window_id="window",
        event_type="STRATEGY_EVALUATION",
        bucket_key="BTC-5m",
        payload={
            "strategy": "DIRECTIONAL_EDGE",
            "action": "TRADE",
            "reason": "TRADE",
            "directional_execution": {
                "router_status": "ROUTED",
                "risk_approved": True,
                "paper_trade_id": "paper-trade-1",
                "paper_fill_status": "FILLED",
            },
        },
        observed_at=datetime(2026, 9, 15, 12, 30, tzinfo=UTC),
    )

    asyncio.run(_assert_filled_trade_is_open_position())


async def _assert_newest_pipeline_event_is_visible() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["latest_observed_at"] == "2026-09-15T12:02:00+00:00"
    assert bucket["fee_status"] == "READY"
    assert bucket["fee_error"] is None
    assert bucket["ptb_status"] == "PTB_READY"
    btc_status = bucket["chainlink"]["per_asset"]["BTC"]
    assert btc_status["parse_success_count"] == 4
    assert btc_status["subscription_snapshot_count"] == 1
    assert btc_status["last_frame_class"] == "LIVE_UPDATE"


async def _assert_newest_strategy_event_is_visible() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["last_abstain_reason"] == "MODEL_UNAVAILABLE"
    assert bucket["model_state"] == "TRAINING_CORPUS_REQUIRED"
    assert bucket["model_version"] == "PAPER_RESEARCH_BASELINE"
    assert bucket["calibration_version"] == "PAPER_RESEARCH_BASELINE_UNPROMOTABLE"
    assert bucket["p_up"] == "0.62"
    assert bucket["p_down"] == "0.38"
    assert bucket["selected_side"] == "UP"
    assert bucket["net_edge"] == "0.21"
    assert bucket["directional_execution"]["risk_approved"] is True


async def _assert_directional_status_contains_decision_audit() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["audit_version"] == "DIRECTIONAL_DECISION_AUDIT_V1"
    assert bucket["first_failing_gate"] == "SIGNAL_STABILITY_GATE"
    assert bucket["final_decision"] == "ABSTAIN"
    assert bucket["final_reason"] == "SIGNAL_UNSTABLE"
    assert bucket["signal_stability"] == "0.60"
    assert bucket["minimum_signal_stability"] == "0.70"
    assert bucket["flip_rate"] == "0.40"
    assert bucket["maximum_flip_rate"] == "0.20"
    assert bucket["counterfactual_net_edge"] == "0.04"
    assert bucket["counterfactual_edge_margin"] == "0.01"
    assert bucket["counterfactual_pricing_available"] is True


async def _assert_directional_audit_endpoint() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/audit?hours=24")
        payload = await response.json()
    finally:
        await client.close()
    assert response.status == 200
    assert payload["status"] == "DIRECTIONAL_DECISION_AUDIT_READY"
    assert payload["version"] == "DIRECTIONAL_DECISION_AUDIT_V1"
    assert payload["total_directional_evaluations"] == 2
    assert payload["reason_counts"]["UNKNOWN_NEW_REASON"] == 1
    assert payload["reason_counts"]["SIGNAL_UNSTABLE"] == 1
    assert payload["audit_payload_coverage"] == {
        "events_with_audit": 1,
        "total_directional_evaluations": 2,
    }
    assert payload["first_failing_gate_counts"]["SIGNAL_STABILITY_GATE"] == 1
    assert payload["signal_stability_distribution"]["median"] == "0.60"
    assert payload["flip_rate_distribution"]["median"] == "0.40"


async def _assert_risk_reject_is_not_open_position() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["state"] == "COOLDOWN_ACTIVE"
    assert bucket["state"] != "PAPER_POSITION_OPEN"
    assert bucket["cooldown_active"] is True
    assert bucket["current_losing_streak"] == 27
    assert bucket["risk_reasons"] == ["CONSECUTIVE_LOSS_COOLDOWN_ACTIVE"]


async def _assert_paper_risk_brake_is_visible() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["state"] == "RISK_BRAKE_ACTIVE"
    assert bucket["state"] != "PAPER_POSITION_OPEN"
    assert bucket["risk_brake_active"] is True
    assert bucket["risk_brake_reason"] == "PAPER_DRAWDOWN_BRAKE_ACTIVE"
    assert bucket["paper_current_equity"] == "30.00"
    assert bucket["open_cost_basis"] == "12.00"
    assert bucket["directional_open_cost_basis"] == "12.00"
    assert bucket["global_open_cost_basis"] == "20.52"
    assert bucket["structural_open_cost_basis"] == "8.52"
    assert bucket["directional_available_capital"] == "18.00"
    assert bucket["open_trade_count"] == 4
    assert bucket["directional_open_trade_count"] == 4
    assert bucket["global_open_trade_count"] == 6
    assert bucket["structural_open_trade_count"] == 2
    assert bucket["same_asset_open_count"] == 1
    assert bucket["recent_directional_win_rate"] == "0.10"
    assert bucket["recent_directional_pnl"] == "-6.00"
    assert bucket["risk_reasons"] == [
        "PAPER_DRAWDOWN_BRAKE_ACTIVE",
        "OPEN_EXPOSURE_LIMIT",
    ]


async def _assert_filled_trade_is_open_position() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["state"] == "PAPER_POSITION_OPEN"
    assert bucket["directional_execution"]["risk_approved"] is True


async def _assert_directional_status_contains_feed_health() -> None:
    app = create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/directional/status")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    bucket = next(item for item in payload["buckets"] if item["asset"] == "BTC")
    assert bucket["official_status"] == "OFFICIAL_REFERENCE_READY"
    assert bucket["proxy_status"] == "PROXY_READY"
    assert bucket["ptb_status"] == "PTB_READY"
    assert bucket["chainlink"]["selected_topic"] == "crypto_prices_twap_sixty"
    assert bucket["chainlink"]["parse_success_count"] == 3
    assert bucket["binance_hourly"]["last_match_status"] == "MATCH"
    assert bucket["label"] == "PAPER / SHADOW — NO REAL ORDER"
