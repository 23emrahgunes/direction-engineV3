import asyncio
from datetime import UTC, datetime

from aiohttp.test_utils import TestClient, TestServer

from direction_engine_v3.app import build_dashboard_snapshot, create_app


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
        ("GET", "/api/paper/trades"),
        ("GET", "/api/paper/trades/{id}"),
        ("GET", "/api/paper/abstains"),
        ("GET", "/api/shadow/status"),
    }
    assert all(method == "GET" for method, _path in routes)


def test_dashboard_root_serves_existing_read_only_html() -> None:
    asyncio.run(_assert_dashboard_root_serves_existing_read_only_html())


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
    assert "direction-engineV3 Dashboard" in body
    assert "PAPER / SHADOW" in body
    assert "PAPER TRADES" in body
    assert "ABSTAINS / REJECTIONS" in body

    snapshot = build_dashboard_snapshot().as_dict()
    assert snapshot["mode"] == {
        "app_mode": "PAPER",
        "live_trading_enabled": False,
        "live_auto_arm": False,
    }
    assert snapshot["execution"]["real_order_submission"] is False
