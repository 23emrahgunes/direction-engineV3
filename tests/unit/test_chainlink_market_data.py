from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.adapters.chainlink import (
    inspect_twap_frame,
    parse_twap,
    parse_twap_from_symbol,
    twap_subscription,
)
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


@pytest.mark.parametrize("asset", [Asset.BTC, Asset.ETH, Asset.SOL, Asset.XRP])
def test_verified_live_twap60_envelope_is_normalized(asset: Asset) -> None:
    twap = parse_twap(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": 1789473600500,
            "payload": {
                "symbol": f"{asset.value.lower()}/usd",
                "window_s": 60,
                "data": [
                    {
                        "timestamp": 1789473600123,
                        "value": "60123.45",
                        "full_accuracy_value": "60123450000000000000000",
                    }
                ],
            },
        },
        asset=asset,
        window_seconds=60,
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=101,
    )
    assert twap.asset is asset
    assert twap.window_seconds == 60
    assert twap.value == Decimal("60123.45")
    assert twap.publisher_ts != twap.lineage.source_ts


def test_twap_asset_can_be_derived_from_returned_symbol() -> None:
    raw = {
        "topic": "crypto_prices_twap_sixty",
        "type": "update",
        "timestamp": 1789473600500,
        "payload": {
            "symbol": "eth/usd",
            "window_s": 60,
            "data": [
                {
                    "timestamp": 1789473600123,
                    "value": "3000.25",
                    "full_accuracy_value": "3000250000000000000000",
                }
            ],
        },
    }

    frame = inspect_twap_frame(raw, window_seconds=60)
    twap = parse_twap_from_symbol(
        raw,
        window_seconds=60,
        recv_ts=RECV,
        normalized_ts=RECV,
        recv_monotonic_ns=101,
    )

    assert frame.asset is Asset.ETH
    assert frame.symbol == "ETH/USD"
    assert frame.data_item_count == 1
    assert frame.data_item_keys == ("full_accuracy_value", "timestamp", "value")
    assert twap.asset is Asset.ETH
    assert twap.value == Decimal("3000.25")


def test_verified_twap_rejects_missing_or_ambiguous_data() -> None:
    base = {
        "topic": "crypto_prices_twap_sixty",
        "type": "update",
        "timestamp": 1789473600500,
        "payload": {
            "symbol": "btc/usd",
            "window_s": 60,
            "data": [],
        },
    }
    with pytest.raises(MarketDataSchemaError, match="exactly one"):
        parse_twap(
            base,
            asset=Asset.BTC,
            window_seconds=60,
            recv_ts=RECV,
            normalized_ts=RECV,
            recv_monotonic_ns=102,
        )
    malformed = dict(base)
    malformed["payload"] = {
        "symbol": "btc/usd",
        "window_s": 60,
        "data": [{"timestamp": 1789473600123, "value": "60000"}],
    }
    with pytest.raises(MarketDataSchemaError, match="full_accuracy_value"):
        parse_twap(
            malformed,
            asset=Asset.BTC,
            window_seconds=60,
            recv_ts=RECV,
            normalized_ts=RECV,
            recv_monotonic_ns=103,
        )
