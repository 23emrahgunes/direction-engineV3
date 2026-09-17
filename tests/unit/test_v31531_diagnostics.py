from datetime import UTC, datetime
from decimal import Decimal

from direction_engine_v3.diagnostics.bucket_pipeline_probe import _ptb_diagnostic_fields
from direction_engine_v3.diagnostics.chainlink_fanout_probe import _sanitize_frame
from direction_engine_v3.diagnostics.rtds_probe import (
    sanitize_message_shape,
    subscription_for,
)
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data.official_runtime import ChainlinkTwapCollector

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


class FakeClock:
    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        return 123


class FakeTransport:
    pass


def test_rtds_probe_subscribes_to_topics_independently() -> None:
    chainlink = subscription_for("crypto_prices_chainlink", Asset.BTC)
    twap = subscription_for("crypto_prices_twap_sixty", Asset.BTC)

    assert chainlink["subscriptions"] != twap["subscriptions"]
    assert chainlink["subscriptions"][0]["topic"] == "crypto_prices_chainlink"
    assert twap["subscriptions"][0]["topic"] == "crypto_prices_twap_sixty"
    assert chainlink["subscriptions"][0]["filters"] == '{"symbol":"btc/usd"}'


def test_rtds_probe_sanitizes_message_shape_and_redacts_sensitive_keys() -> None:
    shape = sanitize_message_shape(
        {
            "topic": "crypto_prices_twap_sixty",
            "payload": {
                "symbol": "btc/usd",
                "full_accuracy_value": Decimal("60000000000000000000000"),
                "api_secret": "must-not-leak",
            },
        }
    )

    assert shape == {
        "payload": {"full_accuracy_value": "decimal", "symbol": "str"},
        "topic": "str",
    }


def test_chainlink_status_records_parse_counts_and_source_timestamp() -> None:
    collector = ChainlinkTwapCollector(FakeTransport(), FakeClock())
    collector.handle_message({"topic": "wrong", "type": "update", "payload": {}})
    collector.handle_message(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": int(NOW.timestamp() * 1000),
            "payload": {
                "symbol": "btc/usd",
                "full_accuracy_value": "60000000000000000000000",
                "timestamp": int(NOW.timestamp() * 1000),
            },
        }
    )

    status = collector.status().as_dict()
    assert status["selected_topic"] == "crypto_prices_twap_sixty"
    assert status["parse_success_count"] == 1
    assert status["parse_failure_count"] == 1
    assert status["message_count"] == 1
    assert status["last_source_timestamp"] == NOW.isoformat()
    assert status["latest_twap_by_asset"] == {"BTC": "60000"}


def test_bucket_probe_distinguishes_unwired_official_service() -> None:
    result = _ptb_diagnostic_fields(
        status="PTB_UNAVAILABLE", reason="OFFICIAL_SERVICE_ABSENT"
    )
    assert result == {
        "official_service_wired": False,
        "ptb_diagnostic_status": "OFFICIAL_SERVICE_NOT_WIRED",
    }


def test_chainlink_fanout_probe_sanitizes_frame_and_reports_filter_mismatch() -> None:
    evidence = _sanitize_frame(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": int(NOW.timestamp() * 1000),
            "payload": {
                "symbol": "eth/usd",
                "window_s": 60,
                "api_secret": "must-not-leak",
                "data": [
                    {
                        "timestamp": int(NOW.timestamp() * 1000),
                        "value": "3000",
                        "full_accuracy_value": "3000000000000000000000",
                    }
                ],
            },
        },
        intended_asset=Asset.BTC,
        clock=FakeClock(),
    )

    assert evidence == {
        "intended_asset": "BTC",
        "returned_topic": "crypto_prices_twap_sixty",
        "returned_type": "update",
        "returned_symbol": "eth/usd",
        "returned_asset": "ETH",
        "payload_keys": ["data", "symbol", "window_s"],
        "data_item_count": 1,
        "data_item_keys": ["full_accuracy_value", "timestamp", "value"],
        "window_s": 60,
        "parse_error": None,
        "filter_status": "FILTER_MISMATCH",
    }
