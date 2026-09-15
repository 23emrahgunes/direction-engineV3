from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    Asset,
    DecisionAction,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OrderSide,
    OutcomeSide,
    ProbabilityForecast,
    ProxyReference,
)
from direction_engine_v3.features import ExternalDirectionalSnapshot, build_directional_features
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    PolymarketBook,
    PolymarketLevel,
    PriceToBeatRecord,
)
from direction_engine_v3.pricing import LiquidityRole, PricingPolicy, simulate_depth
from direction_engine_v3.strategies.directional import (
    CalibrationReadiness,
    DirectionalPolicy,
    assess_directional_edge,
)

START = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
NOW = START + timedelta(minutes=1)
SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"
POLICY = DirectionalPolicy(
    minimum_net_edge=Decimal("0.03"),
    uncertainty_buffer=Decimal("0.01"),
    minimum_signal_stability=Decimal("0.70"),
    maximum_flip_rate=Decimal("0.20"),
    max_forecast_age=timedelta(seconds=2),
)


def market() -> Market:
    return Market(
        "market-1",
        "condition-1",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        (MarketToken("up", OutcomeSide.UP), MarketToken("down", OutcomeSide.DOWN)),
        START,
        START + timedelta(minutes=5),
        SOURCE,
    )


def ptb() -> PriceToBeatRecord:
    reference = OfficialReference(
        "ptb-ref",
        "market-1",
        Asset.BTC,
        Decimal("60000"),
        SOURCE,
        START,
        START + timedelta(milliseconds=50),
        START,
        True,
    )
    return PriceToBeatRecord("condition-1", reference, START, "ptb-row")


def snapshot(*, stability: str = "0.90", flip_rate: str = "0.05") -> ExternalDirectionalSnapshot:
    reference = ProxyReference(
        "proxy-ref",
        "market-1",
        Asset.BTC,
        Decimal("60300"),
        "binance-spot",
        NOW - timedelta(milliseconds=100),
        NOW - timedelta(milliseconds=50),
        NOW - timedelta(milliseconds=100),
    )
    return ExternalDirectionalSnapshot(
        current_reference=reference,
        short_return=Decimal("0.001"),
        medium_return=Decimal("0.003"),
        momentum=Decimal("0.002"),
        realized_volatility=Decimal("0.01"),
        volatility_acceleration=Decimal("0.001"),
        spot_perp_basis=Decimal("0.0002"),
        external_book_imbalance=Decimal("0.30"),
        microprice_distance=Decimal("0.0001"),
        trade_imbalance=Decimal("0.20"),
        signal_stability=Decimal(stability),
        flip_rate=Decimal(flip_rate),
        regime_score=Decimal("0.4"),
        source="binance-normalized",
        source_ts=NOW - timedelta(milliseconds=100),
    )


def features(*, stability: str = "0.90", flip_rate: str = "0.05"):
    return build_directional_features(
        market(),
        ptb(),
        snapshot(stability=stability, flip_rate=flip_rate),
        generated_at=NOW,
        feature_set_version="external-v1",
    )


def forecast(*, p_up: str = "0.70", generated_at: datetime = NOW) -> ProbabilityForecast:
    up = Decimal(p_up)
    return ProbabilityForecast(
        "market-1",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        up,
        Decimal("1") - up,
        "model-v1",
        "cal-v1",
        "external-v1",
        min(NOW, generated_at),
        generated_at,
    )


def calibration(*, ready: bool = True) -> CalibrationReadiness:
    return CalibrationReadiness(Asset.BTC, Horizon.FIVE_MINUTES, "cal-v1", 1_000, ready)


def pricing(token: str, ask: str = "0.55"):
    lineage = EventLineage(DataSource.POLYMARKET_CLOB, NOW, NOW, NOW, 1)
    book = PolymarketBook(
        "condition-1",
        token,
        (PolymarketLevel(Decimal("0.50"), Decimal("10")),),
        (PolymarketLevel(Decimal(ask), Decimal("10")),),
        f"{token}-book",
        Decimal("5"),
        Decimal("0.01"),
        lineage,
    )
    fee = FeeSchedule(
        "condition-1",
        Decimal("0"),
        Decimal("1000"),
        Decimal("0.07"),
        Decimal("1"),
        replace(lineage, source_ts=None),
    )
    return simulate_depth(
        book,
        fee,
        side=OrderSide.BUY,
        requested_quantity=Decimal("5"),
        limit_price=Decimal("1"),
        role=LiquidityRole.TAKER,
        observed_at=NOW,
        policy=PricingPolicy(
            timedelta(seconds=1),
            timedelta(seconds=1),
            Decimal("0"),
            Decimal("0"),
        ),
    )


def test_external_features_include_ptb_tte_and_no_clob_value() -> None:
    vector = features()
    values = {feature.name: feature.value for feature in vector.features}
    assert values["ptb_normalized_distance"] == Decimal("0.005")
    assert values["tte_fraction"] == Decimal("0.8")
    assert "polymarket_price" not in values
    assert "clob_price" not in values


def test_feature_builder_rejects_wrong_reference_and_inactive_market() -> None:
    wrong = replace(snapshot().current_reference, market_id="other")
    with pytest.raises(ValueError, match="reference identity"):
        build_directional_features(
            market(),
            ptb(),
            replace(snapshot(), current_reference=wrong),
            generated_at=NOW,
            feature_set_version="external-v1",
        )
    with pytest.raises(ValueError, match="active"):
        build_directional_features(
            market(),
            ptb(),
            snapshot(),
            generated_at=market().window_end,
            feature_set_version="external-v1",
        )


def test_calibrated_external_edge_emits_pre_risk_candidate() -> None:
    assessment = assess_directional_edge(
        market(),
        ptb(),
        features(),
        forecast(),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW + timedelta(milliseconds=100),
        policy=POLICY,
    )
    assert assessment.action is DecisionAction.TRADE
    assert assessment.selected_side is OutcomeSide.UP
    assert assessment.net_edge is not None and assessment.net_edge > 0
    assert assessment.candidate is not None
    assert assessment.reason == "PRE_RISK_CANDIDATE"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"price_to_beat": None}, "OFFICIAL_PTB_UNAVAILABLE"),
        ({"features": None}, "FEATURES_UNAVAILABLE"),
        ({"forecast": None}, "MODEL_UNAVAILABLE"),
        ({"calibration": None}, "CALIBRATION_NOT_READY"),
        ({"calibration": calibration(ready=False)}, "CALIBRATION_NOT_READY"),
    ],
)
def test_missing_critical_inputs_abstain(overrides: dict[str, object], reason: str) -> None:
    arguments = {
        "price_to_beat": ptb(),
        "features": features(),
        "forecast": forecast(),
        "calibration": calibration(),
    }
    arguments.update(overrides)
    assessment = assess_directional_edge(
        market(),
        arguments["price_to_beat"],  # type: ignore[arg-type]
        arguments["features"],  # type: ignore[arg-type]
        arguments["forecast"],  # type: ignore[arg-type]
        arguments["calibration"],  # type: ignore[arg-type]
        pricing("up"),
        pricing("down"),
        observed_at=NOW + timedelta(milliseconds=100),
        policy=POLICY,
    )
    assert assessment.action is DecisionAction.ABSTAIN
    assert assessment.reason == reason


def test_stale_unstable_flipping_tied_and_low_edge_forecasts_abstain() -> None:
    common = (market(), ptb())
    stale = assess_directional_edge(
        *common,
        features(),
        forecast(generated_at=NOW - timedelta(seconds=3)),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert stale.reason == "FORECAST_STALE_OR_FUTURE"
    unstable = assess_directional_edge(
        *common,
        features(stability="0.60"),
        forecast(),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert unstable.reason == "SIGNAL_UNSTABLE"
    flipping = assess_directional_edge(
        *common,
        features(flip_rate="0.30"),
        forecast(),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert flipping.reason == "FLIP_RATE_TOO_HIGH"
    tied = assess_directional_edge(
        *common,
        features(),
        forecast(p_up="0.50"),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert tied.reason == "NO_DIRECTIONAL_SIDE"
    low = assess_directional_edge(
        *common,
        features(),
        forecast(p_up="0.56"),
        calibration(),
        pricing("up"),
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert low.reason == "NET_EDGE_BELOW_MINIMUM"


def test_selected_side_requires_full_identity_matched_executable_price() -> None:
    wrong = replace(pricing("up"), token_id="other")
    assessment = assess_directional_edge(
        market(),
        ptb(),
        features(),
        forecast(),
        calibration(),
        wrong,
        pricing("down"),
        observed_at=NOW,
        policy=POLICY,
    )
    assert assessment.reason == "EXECUTABLE_PRICE_IDENTITY_MISMATCH"
