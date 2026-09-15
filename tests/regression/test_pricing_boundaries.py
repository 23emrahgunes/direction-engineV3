import inspect

from direction_engine_v3.pricing import DepthSimulation, simulate_depth


def test_pricing_simulator_has_no_order_or_network_operation() -> None:
    forbidden = {"submit", "execute", "place_order", "cancel", "request", "session"}
    names = set(inspect.signature(simulate_depth).parameters)
    assert not names & forbidden
    source = inspect.getsource(simulate_depth).lower()
    assert not any(term in source for term in ("http", "websocket", "order("))


def test_display_price_is_not_an_executable_result_field() -> None:
    assert "display_price" not in DepthSimulation.__dataclass_fields__
