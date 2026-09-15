"""Local read-only dashboard smoke without opening a listening socket."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from direction_engine_v3.app import build_dashboard_snapshot, create_app
from direction_engine_v3.config import APP_MODE, LIVE_AUTO_ARM, LIVE_TRADING_ENABLED


def main() -> None:
    app = create_app()
    snapshot = build_dashboard_snapshot().as_dict()
    routes = {route.resource.canonical for route in app.router.routes()}
    required = {"/health/live", "/health/ready", "/metrics", "/api/dashboard"}
    if not required.issubset(routes):
        raise SystemExit(f"missing dashboard routes: {sorted(required - routes)}")
    mode = snapshot["mode"]
    if mode["app_mode"] != APP_MODE or APP_MODE != "PAPER":
        raise SystemExit("dashboard mode is not PAPER")
    if mode["live_trading_enabled"] is not LIVE_TRADING_ENABLED or LIVE_TRADING_ENABLED:
        raise SystemExit("LIVE trading is enabled")
    if mode["live_auto_arm"] is not LIVE_AUTO_ARM or LIVE_AUTO_ARM:
        raise SystemExit("LIVE auto arm is enabled")
    print("dashboard_smoke=PASS routes=4 mode=PAPER live=false auto_arm=false")


if __name__ == "__main__":
    main()
