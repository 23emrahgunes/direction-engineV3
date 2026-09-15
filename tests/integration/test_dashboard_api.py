from datetime import UTC, datetime

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
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/health/shadow-ready"),
        ("GET", "/health/trading-ready"),
        ("GET", "/metrics"),
        ("GET", "/api/dashboard"),
    }
    assert all(method == "GET" for method, _path in routes)
