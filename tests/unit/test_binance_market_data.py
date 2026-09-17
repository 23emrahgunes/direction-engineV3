from datetime import UTC, datetime
from decimal import Decimal

import pytest

from direction_engine_v3.adapters.binance import (
    parse_aggregate_trade,
    parse_depth_top,
    parse_rest_aggregate_trade,
    stream_url,
)
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import DataSource, MarketDataSchemaError

RECV = datetime(2026, 9, 15, 12, 0, 1, tzinfo=UTC)
NORMALIZED = datetime(2026, 9, 15, 12, 0, 1, 1000, tzinfo=UTC)


def test_supported_stream_urls_are_public_and_scope_closed() -> None:
    assert stream_url(Asset.BTC, "aggTrade") == (
        "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    )
    assert stream_url(Asset.XRP, "depth@100ms", perpetual=True) == (
        "wss://fstream.binance.com/public/ws/xrpusdt@depth@100ms"
    )
    with pytest.raises(ValueError):
        stream_url(Asset.BTC, "orders")


def test_aggregate_trade_preserves_decimal_and_source_time() -> None:
    trade = parse_aggregate_trade(
        {
            "e": "aggTrade",
            "E": 1789473600123,
            "s": "BTCUSDT",
            "a": 42,
            "p": "60123.45000000",
            "q": "0.00120000",
            "m": False,
        },
        asset=Asset.BTC,
        recv_ts=RECV,
        normalized_ts=NORMALIZED,
        recv_monotonic_ns=123,
    )
    assert trade.price == Decimal("60123.45000000")
    assert trade.quantity == Decimal("0.00120000")
    assert trade.lineage.source is DataSource.BINANCE_SPOT
    assert trade.lineage.source_ts.microsecond == 123000


def test_depth_top_carries_update_range_for_sequence_validation() -> None:
    top, first_update_id = parse_depth_top(
        {
            "e": "depthUpdate",
            "E": 1789473600000,
            "s": "ETHUSDT",
            "U": 100,
            "u": 102,
            "b": [["2500.10", "2.5"]],
            "a": [["2500.20", "3.0"]],
        },
        asset=Asset.ETH,
        recv_ts=RECV,
        normalized_ts=NORMALIZED,
        recv_monotonic_ns=124,
    )
    assert first_update_id == 100
    assert top.update_id == 102
    assert top.bid_price < top.ask_price


def test_binance_symbol_mismatch_fails_closed() -> None:
    with pytest.raises(MarketDataSchemaError, match="symbol"):
        parse_aggregate_trade(
            {"e": "aggTrade", "E": 1, "s": "DOGEUSDT", "a": 1, "p": "1", "q": "1", "m": True},
            asset=Asset.BTC,
            recv_ts=RECV,
            normalized_ts=NORMALIZED,
            recv_monotonic_ns=1,
        )


def test_rest_aggregate_trade_fixture_uses_rest_timestamp_contract() -> None:
    trade = parse_rest_aggregate_trade(
        {
            "a": 123456,
            "p": "60123.45000000",
            "q": "0.00120000",
            "f": 123450,
            "l": 123456,
            "T": 1789473600123,
            "m": True,
            "M": True,
        },
        asset=Asset.BTC,
        recv_ts=RECV,
        normalized_ts=NORMALIZED,
        recv_monotonic_ns=125,
    )
    assert trade.asset is Asset.BTC
    assert trade.trade_id == 123456
    assert trade.price == Decimal("60123.45000000")
    assert trade.quantity == Decimal("0.00120000")
    assert trade.buyer_is_maker is True
    assert trade.lineage.source_ts is not None
    assert trade.lineage.source_ts.microsecond == 123000


def test_rest_trade_requires_rest_fields_and_rejects_ws_only_shape() -> None:
    with pytest.raises(MarketDataSchemaError, match="T"):
        parse_rest_aggregate_trade(
            {"a": 1, "p": "1", "q": "1", "m": False},
            asset=Asset.BTC,
            recv_ts=RECV,
            normalized_ts=NORMALIZED,
            recv_monotonic_ns=126,
        )
    with pytest.raises(MarketDataSchemaError, match="s must"):
        parse_aggregate_trade(
            {"a": 1, "p": "1", "q": "1", "T": 1, "m": False},
            asset=Asset.BTC,
            recv_ts=RECV,
            normalized_ts=NORMALIZED,
            recv_monotonic_ns=127,
        )
