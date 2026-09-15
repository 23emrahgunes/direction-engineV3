"""VPS watchdog-style status check for safe defaults."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from direction_engine_v3.app import build_dashboard_snapshot


def main() -> None:
    payload = build_dashboard_snapshot().as_dict()
    mode = payload["mode"]
    execution = payload["execution"]
    if mode["app_mode"] != "PAPER":
        raise SystemExit("APP_MODE is not PAPER")
    if mode["live_trading_enabled"] is not False:
        raise SystemExit("LIVE_TRADING_ENABLED is not false")
    if mode["live_auto_arm"] is not False:
        raise SystemExit("LIVE_AUTO_ARM is not false")
    if execution["real_order_submission"] is not False:
        raise SystemExit("real order submission is exposed")
    print("watchdog_status=PASS paper=true live=false auto_arm=false real_orders=false")


if __name__ == "__main__":
    main()
