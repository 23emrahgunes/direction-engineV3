from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.adapters.chainlink import parse_twap, twap_subscription
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import MarketDataSchemaError

RECV = datetime(2026, 9, 15, 12, 0, 1, tzinfo=UTC)


def test_subscription_uses_exact_asset_and_window() -> None:
    subscription = twap_subscription(Asset.SOL, window_seconds=30)
    assert subscription["subscriptions"] == [
        {
            "topic": "crypto_prices_twap_thirty",
            "type": "update",
            "filters": '{"symbol":"sol/usd"}',
        }
    ]


def test_twap_uses_full_accuracy_e18_and_separate_timestamps() -> None:
    twap = parse_twap(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": 1789473600500,
            "payload": {
                "symbol": "xrp/usd",
                "full_accuracy_value": "3123456789012345678",
                "timestamp": 1789473600123,
            },
        },
        asset=Asset.XRP,
        window_seconds=60,
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=100,
    )
    assert twap.value == Decimal("3.123456789012345678")
    assert twap.publisher_ts != twap.lineage.source_ts


def test_twap_never_falls_back_to_rounded_value() -> None:
    with pytest.raises(MarketDataSchemaError, match="full_accuracy_value"):
        parse_twap(
            {
                "topic": "crypto_prices_twap_thirty",
                "type": "update",
                "timestamp": 1789473600500,
                "payload": {"symbol": "btc/usd", "value": "60000", "timestamp": 1789473600123},
            },
            asset=Asset.BTC,
            window_seconds=30,
            recv_ts=RECV,
            normalized_ts=RECV,
            recv_monotonic_ns=100,
        )
