"""Domain tests for market identity, references, books, and features."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    Asset,
    BookLevel,
    FeatureValue,
    FeatureVector,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OrderBookSnapshot,
    OutcomeSide,
    ProbabilityForecast,
    ProxyReference,
    ReferenceKind,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def make_market() -> Market:
    return Market(
        market_id="btc-updown-5m-20260915t1200z",
        condition_id="condition-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        tokens=(
            MarketToken(token_id="up-token", outcome=OutcomeSide.UP),
            MarketToken(token_id="down-token", outcome=OutcomeSide.DOWN),
        ),
        window_start=NOW,
        window_end=NOW + timedelta(minutes=5),
        settlement_source="chainlink-btc-usd",
    )


def test_market_is_immutable_and_has_exact_binary_mapping() -> None:
    market = make_market()

    assert tuple(token.outcome for token in market.tokens) == (
        OutcomeSide.UP,
        OutcomeSide.DOWN,
    )
    with pytest.raises(FrozenInstanceError):
        market.market_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "tokens",
    [
        (MarketToken("up-1", OutcomeSide.UP),),
        (
            MarketToken("up-1", OutcomeSide.UP),
            MarketToken("up-2", OutcomeSide.UP),
        ),
        (
            MarketToken("same", OutcomeSide.UP),
            MarketToken("same", OutcomeSide.DOWN),
        ),
    ],
)
def test_market_rejects_invalid_token_mappings(tokens: tuple[MarketToken, ...]) -> None:
    with pytest.raises(ValueError):
        Market(
            market_id="market-1",
            condition_id="condition-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            tokens=tokens,
            window_start=NOW,
            window_end=NOW + timedelta(minutes=5),
            settlement_source="official-source",
        )


def test_market_rejects_naive_or_non_increasing_windows() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        Market(
            market_id="market-1",
            condition_id="condition-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            tokens=make_market().tokens,
            window_start=datetime(2026, 9, 15, 12, 0),
            window_end=NOW + timedelta(minutes=5),
            settlement_source="official-source",
        )

    with pytest.raises(ValueError, match="window_start"):
        Market(
            market_id="market-1",
            condition_id="condition-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            tokens=make_market().tokens,
            window_start=NOW,
            window_end=NOW,
            settlement_source="official-source",
        )


def test_official_and_proxy_references_are_distinct_types_and_kinds() -> None:
    common = {
        "reference_id": "reference-1",
        "market_id": "market-1",
        "asset": Asset.BTC,
        "value": Decimal("60000"),
        "source": "source-1",
        "source_ts": NOW,
        "recv_ts": NOW + timedelta(milliseconds=10),
        "effective_ts": NOW,
    }

    official = OfficialReference(**common, is_price_to_beat=True)
    proxy = ProxyReference(**common)

    assert type(official) is not type(proxy)
    assert official.kind is ReferenceKind.OFFICIAL
    assert proxy.kind is ReferenceKind.PROXY
    assert not isinstance(proxy, OfficialReference)


@pytest.mark.parametrize("invalid_value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_references_reject_non_finite_or_non_positive_values(invalid_value: Decimal) -> None:
    with pytest.raises(ValueError):
        ProxyReference(
            reference_id="reference-1",
            market_id="market-1",
            asset=Asset.BTC,
            value=invalid_value,
            source="binance-spot",
            source_ts=NOW,
            recv_ts=NOW,
            effective_ts=NOW,
        )


def test_order_book_requires_sorted_uncrossed_immutable_depth() -> None:
    snapshot = OrderBookSnapshot(
        market_id="market-1",
        token_id="up-token",
        bids=(BookLevel(Decimal("0.49"), Decimal("10")),),
        asks=(BookLevel(Decimal("0.51"), Decimal("12")),),
        source="polymarket-clob",
        source_ts=NOW,
        recv_ts=NOW + timedelta(milliseconds=20),
        sequence=10,
    )

    assert snapshot.bids[0].price == Decimal("0.49")
    with pytest.raises(FrozenInstanceError):
        snapshot.sequence = 11  # type: ignore[misc]


@pytest.mark.parametrize(
    ("bids", "asks", "message"),
    [
        (
            (
                BookLevel(Decimal("0.48"), Decimal("1")),
                BookLevel(Decimal("0.49"), Decimal("1")),
            ),
            (),
            "descending",
        ),
        (
            (),
            (
                BookLevel(Decimal("0.52"), Decimal("1")),
                BookLevel(Decimal("0.51"), Decimal("1")),
            ),
            "ascending",
        ),
        (
            (BookLevel(Decimal("0.51"), Decimal("1")),),
            (BookLevel(Decimal("0.50"), Decimal("1")),),
            "locked or crossed",
        ),
    ],
)
def test_order_book_rejects_invalid_ordering(
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OrderBookSnapshot(
            market_id="market-1",
            token_id="up-token",
            bids=bids,
            asks=asks,
            source="polymarket-clob",
            source_ts=NOW,
            recv_ts=NOW,
        )


@pytest.mark.parametrize("invalid_value", [Decimal("NaN"), Decimal("Infinity")])
def test_book_levels_reject_non_finite_values(invalid_value: Decimal) -> None:
    with pytest.raises(ValueError):
        BookLevel(price=invalid_value, quantity=Decimal("1"))
    with pytest.raises(ValueError):
        BookLevel(price=Decimal("0.5"), quantity=invalid_value)


def test_feature_vector_rejects_future_or_duplicate_features() -> None:
    present = FeatureValue("return_5s", Decimal("0.001"), "binance", NOW)
    future = FeatureValue(
        "imbalance_5s",
        Decimal("0.2"),
        "binance",
        NOW + timedelta(milliseconds=1),
    )

    with pytest.raises(ValueError, match="after generated_at"):
        FeatureVector(
            market_id="market-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            feature_set_version="features-v1",
            features=(present, future),
            generated_at=NOW,
        )
    with pytest.raises(ValueError, match="unique"):
        FeatureVector(
            market_id="market-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            feature_set_version="features-v1",
            features=(present, present),
            generated_at=NOW,
        )


def test_probability_forecast_is_calibrated_binary_snapshot() -> None:
    forecast = ProbabilityForecast(
        market_id="market-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        p_up=Decimal("0.61"),
        p_down=Decimal("0.39"),
        model_version="model-v1",
        calibration_version="calibration-v1",
        feature_set_version="features-v1",
        feature_as_of=NOW,
        generated_at=NOW + timedelta(milliseconds=5),
    )

    assert forecast.p_up + forecast.p_down == Decimal("1")


@pytest.mark.parametrize(
    ("p_up", "p_down"),
    [
        (Decimal("NaN"), Decimal("0")),
        (Decimal("1.1"), Decimal("-0.1")),
        (Decimal("0.6"), Decimal("0.5")),
    ],
)
def test_probability_forecast_rejects_invalid_probabilities(
    p_up: Decimal,
    p_down: Decimal,
) -> None:
    with pytest.raises(ValueError):
        ProbabilityForecast(
            market_id="market-1",
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
            p_up=p_up,
            p_down=p_down,
            model_version="model-v1",
            calibration_version="calibration-v1",
            feature_set_version="features-v1",
            feature_as_of=NOW,
            generated_at=NOW,
        )
