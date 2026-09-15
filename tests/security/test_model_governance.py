"""Security boundaries for probability artifacts and promotion readiness."""

import inspect

from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.models import calibration, contracts, empty_registry, logistic, registry


def test_unpromoted_registry_fails_closed_for_all_twelve_buckets() -> None:
    registry = empty_registry()

    assert tuple(state.bucket for state in registry.states) == SUPPORTED_MARKET_BUCKETS
    assert all(not state.readiness.ready for state in registry.states)
    assert all(state.artifact is None for state in registry.states)
    assert all(state.calibrator is None for state in registry.states)


def test_model_package_has_no_network_order_or_unsafe_artifact_loading() -> None:
    source = "\n".join(
        inspect.getsource(module).lower()
        for module in (calibration, contracts, logistic, registry)
    )

    for forbidden in (
        "aiohttp",
        "requests",
        "socket",
        "submit_order",
        "place_order",
        "pickle",
        "joblib",
    ):
        assert forbidden not in source
