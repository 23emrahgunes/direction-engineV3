from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.adapters.polymarket import (
    market_subscription,
    parse_clob_book,
    parse_fee_schedule,
    parse_gamma_market,
    parse_market_resolved,
)
from direction_engine_v3.domain import Asset, Horizon, OutcomeSide
from direction_engine_v3.market_data import MarketDataSchemaError

RECV = datetime(2026, 9, 15, 12, 0, 1, tzinfo=UTC)


def test_gamma_discovery_maps_token_ids_by_outcome_label() -> None:
    market = parse_gamma_market(
        {
            "id": "market-1",
            "conditionId": "condition-1",
            "outcomes": '["Down", "Up"]',
            "clobTokenIds": '["down-token", "up-token"]',
            "startDate": "2026-09-15T12:00:00Z",
            "endDate": "2026-09-15T12:05:00Z",
            "resolutionSource": "https://example.invalid/rules",
        },
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
    )
    assert {token.outcome: token.token_id for token in market.tokens} == {
        OutcomeSide.UP: "up-token",
        OutcomeSide.DOWN: "down-token",
    }


def test_gamma_discovery_rejects_ambiguous_outcomes() -> None:
    with pytest.raises(MarketDataSchemaError, match="UP and DOWN"):
        parse_gamma_market(
            {
                "id": "market-1",
                "conditionId": "condition-1",
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["yes", "no"],
                "startDate": "2026-09-15T12:00:00Z",
                "endDate": "2026-09-15T12:05:00Z",
                "resolutionSource": "rules",
            },
            asset=Asset.BTC,
            horizon=Horizon.FIVE_MINUTES,
        )


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
