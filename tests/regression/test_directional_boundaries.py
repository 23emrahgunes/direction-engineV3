import inspect

from direction_engine_v3.features import build_directional_features
from direction_engine_v3.strategies.directional import assess_directional_edge


def test_directional_alpha_builder_has_no_polymarket_price_input() -> None:
    names = set(inspect.signature(build_directional_features).parameters)
    assert not names & {"book", "clob", "market_price", "polymarket_price"}


def test_directional_strategy_has_no_order_side_effect_or_recovery_sizing() -> None:
    source = inspect.getsource(assess_directional_edge).lower()
    for forbidden in ("submit", "place_order", "martingale", "loss_recovery"):
        assert forbidden not in source
