import inspect

from direction_engine_v3.strategies.structural_arb import scan_complete_set


def test_structural_scanner_is_model_free_and_has_no_order_side_effect() -> None:
    signature = inspect.signature(scan_complete_set)
    assert "forecast" not in signature.parameters
    source = inspect.getsource(scan_complete_set)
    for forbidden in (
        "ProbabilityForecast",
        "direction_engine_v3.models",
        "http",
        "websocket",
        "submit",
    ):
        assert forbidden not in source
