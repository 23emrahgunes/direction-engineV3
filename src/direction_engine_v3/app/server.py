"""Read-only aiohttp dashboard API.

No network sockets are opened during import; callers explicitly run the app.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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

DASHBOARD_INDEX_HTML = web.AppKey("dashboard_index_html", str)
DASHBOARD_API_EXECUTOR = web.AppKey("dashboard_api_executor", ThreadPoolExecutor)
API_RESPONSE_TIMEOUT_SECONDS = 5.0


def dashboard_index_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "dashboard" / "web" / "index.html"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("dashboard/web/index.html was not found")


def dashboard_index_html() -> str:
    """Load the small dashboard shell once per request without aiohttp sendfile.

    The SSM-forwarded dashboard is a single static HTML shell. Returning it as an
    in-memory response avoids a per-refresh FileResponse/sendfile path that can
    block the single aiohttp event loop if the VPS filesystem is under I/O
    pressure.
    """

    return dashboard_index_path().read_text(encoding="utf-8")


async def dashboard_index(request: web.Request) -> web.Response:
    return web.Response(text=request.app[DASHBOARD_INDEX_HTML], content_type="text/html")


async def dashboard_api_executor(app: web.Application) -> AsyncIterator[None]:
    """Run blocking SQLite snapshot builders outside the aiohttp event loop.

    The dashboard is accessed through SSM port forwarding and browser refreshes.
    If a synchronous SQLite/file read blocks in the main event loop, even
    ``GET /`` and ``/health/live`` become unreachable.  Keep the HTML and health
    surface responsive by isolating read-only snapshot builders in a bounded
    executor and returning an explicit timeout response when the data path is
    unhealthy.
    """

    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dashboard-api")
    app[DASHBOARD_API_EXECUTOR] = executor
    try:
        yield
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


async def _json_from_builder(
    request: web.Request,
    builder: Callable[..., object],
    /,
    *args: object,
    **kwargs: object,
) -> web.Response:
    if DASHBOARD_API_EXECUTOR not in request.app:
        return web.json_response(builder(*args, **kwargs))
    loop = asyncio.get_running_loop()
    call = partial(builder, *args, **kwargs)
    try:
        payload = await asyncio.wait_for(
            loop.run_in_executor(request.app[DASHBOARD_API_EXECUTOR], call),
            timeout=API_RESPONSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return web.json_response(
            {
                "status": "API_TIMEOUT",
                "reason": "dashboard read-only data builder exceeded timeout",
                "real_order_submission": False,
            },
            status=503,
        )
    except Exception as exc:
        return web.json_response(
            {
                "status": "API_UNAVAILABLE",
                "reason": type(exc).__name__,
                "real_order_submission": False,
            },
            status=503,
        )
    return web.json_response(payload)


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


async def dashboard(request: web.Request) -> web.Response:
    return await _json_from_builder(request, lambda: build_dashboard_snapshot().as_dict())


async def paper_summary(request: web.Request) -> web.Response:
    return await _json_from_builder(request, build_paper_summary)


async def paper_performance(request: web.Request) -> web.Response:
    return await _json_from_builder(
        request,
        build_paper_performance,
        strategy=request.query.get("strategy", "DIRECTIONAL_EDGE"),
    )


async def paper_trades(request: web.Request) -> web.Response:
    return await _json_from_builder(
        request,
        list_paper_trades,
        asset=request.query.get("asset"),
        horizon=request.query.get("horizon"),
        strategy=request.query.get("strategy"),
        side=request.query.get("side"),
        status=request.query.get("status"),
        win_loss=request.query.get("win_loss"),
        limit=request.query.get("limit"),
        offset=request.query.get("offset"),
    )


async def paper_trade_detail(request: web.Request) -> web.Response:
    response = await _json_from_builder(
        request,
        _paper_trade_detail_payload,
        request.match_info["id"],
    )
    if response.status != 200:
        return response
    trade = response.text
    if trade == "null":
        return web.json_response({"error": "paper trade not found"}, status=404)
    return response


def _paper_trade_detail_payload(trade_id: str) -> dict[str, object] | None:
    trade = get_paper_trade(trade_id)
    if trade is None:
        return None
    return trade


async def paper_abstains(request: web.Request) -> web.Response:
    return await _json_from_builder(
        request,
        list_paper_abstains,
        reason=request.query.get("reason"),
        strategy=request.query.get("strategy"),
        asset=request.query.get("asset"),
        horizon=request.query.get("horizon"),
        limit=request.query.get("limit"),
        offset=request.query.get("offset"),
    )


async def shadow_status(request: web.Request) -> web.Response:
    return await _json_from_builder(request, build_shadow_status)


async def directional_status(request: web.Request) -> web.Response:
    return await _json_from_builder(request, build_directional_runtime_status)


def create_app() -> web.Application:
    app = web.Application()
    app.cleanup_ctx.append(dashboard_api_executor)
    app[DASHBOARD_INDEX_HTML] = dashboard_index_html()
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
