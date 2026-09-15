"""Read-only VPS dashboard status probe."""

from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _get(path: str) -> tuple[int, str]:
    try:
        with urlopen(f"http://127.0.0.1:8130{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except URLError as exc:
        raise SystemExit(f"dashboard_unreachable={exc}") from exc


def main() -> None:
    live_status, _live_payload = _get("/health/live")
    ready_status, ready_payload = _get("/health/ready")
    dashboard_status, dashboard_payload = _get("/api/dashboard")
    if live_status != 200:
        raise SystemExit(f"live_status={live_status}")
    if ready_status not in {200, 503}:
        raise SystemExit(f"ready_status={ready_status}")
    if dashboard_status != 200:
        raise SystemExit(f"dashboard_status={dashboard_status}")
    forbidden = ("private_key", "api_secret", "passphrase")
    combined = f"{ready_payload}\n{dashboard_payload}".lower()
    leaked = [item for item in forbidden if item in combined]
    if leaked:
        raise SystemExit(f"dashboard_payload_contains_sensitive_marker={leaked}")
    print(f"dashboard_status=PASS live={live_status} ready={ready_status} dashboard=200")


if __name__ == "__main__":
    main()
