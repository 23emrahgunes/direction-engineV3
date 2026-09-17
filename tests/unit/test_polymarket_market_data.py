from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.adapters.polymarket import (
    market_subscription,
    parse_clob_book,
    parse_fee_rate,
    parse_fee_schedule,
    parse_gamma_market,
    parse_gamma_market_discovery,
    parse_market_resolved,
)
from direction_engine_v3.domain import Asset, Horizon, OutcomeSide
from direction_engine_v3.market_data import MarketDataSchemaError

RECV = datetime(2026, 9, 15, 12, 0, 1, tzinfo=UTC)


def gamma_payload() -> dict[str, object]:
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "question": "Bitcoin Up or Down - September 15, 8:00AM-8:05AM ET",
        "slug": "btc-updown-5m-1789473600",
        "outcomes": '["Down", "Up"]',
        "clobTokenIds": '["down-token", "up-token"]',
        "startDate": "2026-09-14T12:00:00Z",
        "eventStartTime": "2026-09-15T12:00:00Z",
        "endDate": "2026-09-15T12:05:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": "Resolve from the BTC/USD Chainlink TWAP stream.",
        "cryptoMarketConfigId": "btc-5m-twap-60",
        "cryptoMarketConfig": {
            "id": "btc-5m-twap-60",
            "asset": "btc",
            "duration": "5m",
            "twapEnabled": True,
            "twapLookbackSeconds": 60,
        },
        "version": "v1",
        "updatedAt": "2026-09-15T11:59:59Z",
    }


def test_gamma_discovery_maps_token_ids_by_outcome_label() -> None:
    market = parse_gamma_market(
        gamma_payload(),
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
    )
    assert {token.outcome: token.token_id for token in market.tokens} == {
        OutcomeSide.UP: "up-token",
        OutcomeSide.DOWN: "down-token",
    }


def test_gamma_discovery_rejects_ambiguous_outcomes() -> None:
    payload = gamma_payload()
    payload["outcomes"] = ["Yes", "No"]
    payload["clobTokenIds"] = ["yes", "no"]
    with pytest.raises(MarketDataSchemaError, match="UP and DOWN"):
        parse_gamma_market(
            payload,
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
        )


def test_gamma_discovery_uses_event_start_not_creation_start() -> None:
    market = parse_gamma_market(
        gamma_payload(), asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES
    )
    assert market.window_start == datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_gamma_discovery_rejects_wrong_config_asset_or_duration() -> None:
    payload = gamma_payload()
    config = dict(payload["cryptoMarketConfig"])  # type: ignore[arg-type]
    config["asset"] = "eth"
    payload["cryptoMarketConfig"] = config
    with pytest.raises(MarketDataSchemaError, match="asset"):
        parse_gamma_market(payload, asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES)


def test_gamma_discovery_retains_settlement_metadata_lineage() -> None:
    discovery = parse_gamma_market_discovery(
        gamma_payload(),
        event_id="event-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=9,
    )
    assert discovery.settlement.configuration_id == "btc-5m-twap-60"
    assert discovery.settlement.reference_period_seconds == 60
    assert discovery.settlement.lineage.source_ts == datetime(
        2026, 9, 15, 11, 59, 59, tzinfo=UTC
    )


def test_hourly_discovery_uses_binance_candle_metadata_without_twap_config() -> None:
    payload = gamma_payload()
    payload.update(
        {
            "question": "Bitcoin Up or Down - September 15, 8AM ET",
            "slug": "bitcoin-up-or-down-september-15-2026-8am-et",
            "eventStartTime": "2026-09-15T12:00:00Z",
            "endDate": "2026-09-15T13:00:00Z",
            "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
            "cryptoMarketConfigId": None,
            "cryptoMarketConfig": None,
        }
    )
    discovery = parse_gamma_market_discovery(
        payload,
        event_id="hourly-event",
        asset=Asset.BTC,
        horizon=Horizon.ONE_HOUR,
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=10,
    )
    assert discovery.settlement.configuration_id is None
    assert discovery.settlement.reference_period_seconds == 3_600


def test_clob_book_is_decimal_sorted_and_timestamped() -> None:
    book = parse_clob_book(
        {
            "market": "condition-1",
            "asset_id": "up-token",
            "timestamp": "1789473600123",
            "hash": "checksum",
            "bids": [{"price": "0.40", "size": "10"}, {"price": "0.45", "size": "2"}],
            "asks": [{"price": "0.60", "size": "10"}, {"price": "0.55", "size": "2"}],
            "min_order_size": "5",
            "tick_size": "0.01",
        },
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=10,
    )
    assert [level.price for level in book.bids] == [Decimal("0.45"), Decimal("0.40")]
    assert [level.price for level in book.asks] == [Decimal("0.55"), Decimal("0.60")]


def test_dynamic_fee_schedule_retains_current_lineage() -> None:
    fee = parse_fee_schedule(
        {
            "mbf": 0,
            "tbf": 0,
            "fd": {"r": "0.25", "e": "2", "to": True},
        },
        condition_id="condition-1",
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=11,
    )
    assert fee.rate == Decimal("0.25")
    assert fee.exponent == Decimal("2")
    assert fee.lineage.source_ts is None


def test_token_fee_rate_parses_base_fee_without_dynamic_formula() -> None:
    fee = parse_fee_rate(
        {"base_fee": 1000},
        condition_id="condition-1",
        token_id="up-token",
        expected_token_id="up-token",
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=12,
    )
    assert fee.taker_base_bps == Decimal("1000")
    assert fee.taker_fee_mode == "bps"
    with pytest.raises(MarketDataSchemaError, match="base_fee"):
        parse_fee_rate(
            {},
            condition_id="condition-1",
            token_id="up-token",
            expected_token_id="up-token",
            recv_ts=RECV,
            normalized_ts=RECV,
            recv_monotonic_ns=13,
        )


def test_market_subscription_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="unique"):
        market_subscription(("token", "token"))


def test_resolution_event_is_not_conflated_with_reconciled_settlement() -> None:
    event = parse_market_resolved(
        {
            "event_type": "market_resolved",
            "id": "market-1",
            "market": "condition-1",
            "assets_ids": ["up-token", "down-token"],
            "winning_asset_id": "up-token",
            "winning_outcome": "Up",
            "timestamp": "1789473900000",
        },
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=12,
    )
    assert event.winning_token_id == "up-token"
    assert event.winning_outcome == "Up"


def test_resolution_event_requires_a_winning_outcome() -> None:
    with pytest.raises(MarketDataSchemaError, match="winning_outcome"):
        parse_market_resolved(
            {
                "event_type": "market_resolved",
                "id": "market-1",
                "market": "condition-1",
                "winning_asset_id": "up-token",
                "winning_outcome": "",
                "timestamp": "1789473900000",
            },
            recv_ts=RECV,
            normalized_ts=RECV,
            recv_monotonic_ns=13,
        )
