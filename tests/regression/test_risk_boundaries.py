import inspect

from direction_engine_v3.risk import assess_candidate


def test_risk_engine_has_no_order_side_effect_or_recovery_sizing() -> None:
    source = inspect.getsource(assess_candidate).lower()
    for forbidden in (
        "submit",
        "place_order",
        "martingale",
        "recovery",
        "kelly",
        "socket",
        "requests",
    ):
        assert forbidden not in source
