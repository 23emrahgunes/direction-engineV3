"""Read-only aiohttp dashboard API.

No network sockets are opened during import; callers explicitly run the app.
"""

from pathlib import Path

from aiohttp import web

from direction_engine_v3.app.dashboard import (
    build_dashboard_snapshot,
    build_directional_runtime_status,
    build_paper_performance,
    build_paper_summary,
    build_shadow_status,
    get_paper_trade,
    list_paper_abstains,
    list_paper_trades,
)


def dashboard_index_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "dashboard" / "web" / "index.html"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("dashboard/web/index.html was not found")


async def dashboard_index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(dashboard_index_path())


async def live(_request: web.Request) -> web.Response:
    return web.json_response({"alive": True})


async def ready(_request: web.Request) -> web.Response:
    readiness = build_dashboard_snapshot().readiness
    status = 200 if readiness.ready else 503
    return web.json_response(readiness.as_dict(), status=status)


async def shadow_ready(_request: web.Request) -> web.Response:
    snapshot = build_dashboard_snapshot()
    return web.json_response(
        {
            "ready": True,
            "mode": snapshot.as_dict()["mode"],
            "shadow_collection": "REPORTING_READY",
        }
    )


async def trading_ready(_request: web.Request) -> web.Response:
    readiness = build_dashboard_snapshot().readiness
    return web.json_response(readiness.as_dict(), status=503)


async def metrics(_request: web.Request) -> web.Response:
    return web.json_response(build_dashboard_snapshot().metrics.as_dict())


async def dashboard(_request: web.Request) -> web.Response:
    return web.json_response(build_dashboard_snapshot().as_dict())


async def paper_summary(_request: web.Request) -> web.Response:
    return web.json_response(build_paper_summary())


async def paper_performance(request: web.Request) -> web.Response:
    return web.json_response(
        build_paper_performance(strategy=request.query.get("strategy", "DIRECTIONAL_EDGE"))
    )


async def paper_trades(request: web.Request) -> web.Response:
    return web.json_response(
        list_paper_trades(
            asset=request.query.get("asset"),
            horizon=request.query.get("horizon"),
            strategy=request.query.get("strategy"),
            side=request.query.get("side"),
            status=request.query.get("status"),
            win_loss=request.query.get("win_loss"),
            limit=request.query.get("limit"),
            offset=request.query.get("offset"),
        )
    )


async def paper_trade_detail(request: web.Request) -> web.Response:
    trade = get_paper_trade(request.match_info["id"])
    if trade is None:
        return web.json_response({"error": "paper trade not found"}, status=404)
    return web.json_response(trade)


async def paper_abstains(request: web.Request) -> web.Response:
    return web.json_response(
        list_paper_abstains(
            reason=request.query.get("reason"),
            strategy=request.query.get("strategy"),
            asset=request.query.get("asset"),
            horizon=request.query.get("horizon"),
            limit=request.query.get("limit"),
            offset=request.query.get("offset"),
        )
    )


async def shadow_status(_request: web.Request) -> web.Response:
    return web.json_response(build_shadow_status())


async def directional_status(_request: web.Request) -> web.Response:
    return web.json_response(build_directional_runtime_status())


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", dashboard_index, allow_head=False)
    app.router.add_get("/health/live", live, allow_head=False)
    app.router.add_get("/health/ready", ready, allow_head=False)
    app.router.add_get("/health/shadow-ready", shadow_ready, allow_head=False)
    app.router.add_get("/health/trading-ready", trading_ready, allow_head=False)
    app.router.add_get("/metrics", metrics, allow_head=False)
    app.router.add_get("/api/dashboard", dashboard, allow_head=False)
    app.router.add_get("/api/paper/summary", paper_summary, allow_head=False)
    app.router.add_get("/api/paper/performance", paper_performance, allow_head=False)
    app.router.add_get("/api/paper/trades", paper_trades, allow_head=False)
    app.router.add_get("/api/paper/trades/{id}", paper_trade_detail, allow_head=False)
    app.router.add_get("/api/paper/abstains", paper_abstains, allow_head=False)
    app.router.add_get("/api/shadow/status", shadow_status, allow_head=False)
    app.router.add_get("/api/directional/status", directional_status, allow_head=False)
    return app


def main() -> None:
    web.run_app(create_app(), host="127.0.0.1", port=8130)


if __name__ == "__main__":
    main()
