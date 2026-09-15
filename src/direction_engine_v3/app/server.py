"""Read-only aiohttp dashboard API.

No network sockets are opened during import; callers explicitly run the app.
"""

from aiohttp import web

from direction_engine_v3.app.dashboard import build_dashboard_snapshot


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


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health/live", live, allow_head=False)
    app.router.add_get("/health/ready", ready, allow_head=False)
    app.router.add_get("/health/shadow-ready", shadow_ready, allow_head=False)
    app.router.add_get("/health/trading-ready", trading_ready, allow_head=False)
    app.router.add_get("/metrics", metrics, allow_head=False)
    app.router.add_get("/api/dashboard", dashboard, allow_head=False)
    return app


def main() -> None:
    web.run_app(create_app(), host="127.0.0.1", port=8130)


if __name__ == "__main__":
    main()
