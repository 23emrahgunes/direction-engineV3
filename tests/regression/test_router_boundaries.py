import inspect

from direction_engine_v3.router import route_opportunities


def test_router_has_no_network_order_or_strategy_logic() -> None:
    source = inspect.getsource(route_opportunities).lower()
    for forbidden in ("submit", "place_order", "socket", "requests", "martingale"):
        assert forbidden not in source
