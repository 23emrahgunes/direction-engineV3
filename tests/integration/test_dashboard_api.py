import asyncio
import time
from datetime import UTC, datetime

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
        ("GET", "/api/paper/performance"),
        ("GET", "/api/paper/trades"),
        ("GET", "/api/paper/trades/{id}"),
        ("GET", "/api/paper/abstains"),
        ("GET", "/api/shadow/status"),
        ("GET", "/api/directional/status"),
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
    assert "NO REAL ORDER" in body
    assert "APP_MODE=PAPER" in body
    assert "LIVE_TRADING_ENABLED=false" in body
    assert "real_order_submission=false" in body
    assert "Last updated" in body
    assert "Overview" in body
    assert "PAPER RUN" in body
    assert "40 USDC CLEAN BURN-IN" in body
    assert "Run ID" in body
    assert "Settlement / Data Health" in body
    assert "12 Bucket Performance" in body
    assert "PAPER TRADES" in body
    assert "ABSTAINS / REJECTIONS" in body
    assert "DIRECTIONAL EDGE" in body
    assert "STRUCTURAL ARB" in body
    assert "DATA / LATENCY" in body
    assert "Raw JSON" in body
    assert "<pre id=\"overview\"" not in body
    assert "Loading..." not in body
    assert "AbortController" in body
    assert "timeout" in body
    assert "state.refreshing" in body
    assert "batch=1" in body
    assert "names.slice(i,i+batch)" in body
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
    assert "/api/paper/trades?strategy=DIRECTIONAL_EDGE&limit=100" in body
    assert "/api/paper/abstains" in body
    assert "/api/directional/status" in body
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
    assert blocked_response.status == 503
    assert blocked_payload == {
        "status": "API_TIMEOUT",
        "reason": "dashboard read-only data builder exceeded timeout",
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
