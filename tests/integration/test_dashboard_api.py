import asyncio
import time
from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer

from direction_engine_v3.app import build_dashboard_snapshot, create_app
from direction_engine_v3.app import server as dashboard_server
from direction_engine_v3.shadow.storage import SQLiteShadowRepository


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
        ("GET", "/api/model/governance"),
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
    assert "Model Governance / Structural Arb / Data / Read-only API Status" in body
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
