import asyncio
import json
from datetime import UTC, datetime

from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data.official_runtime import ChainlinkTwapCollector

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
FULL_ACCURACY_BY_ASSET = {
    "BTC": "60000000000000000000000",
    "ETH": "3000000000000000000000",
    "SOL": "150000000000000000000",
    "XRP": "3000000000000000000",
}


class Clock:
    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        return 1


class FanoutTransport:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.subscriptions: list[dict[str, object]] = []

    async def resilient_websocket_json(self, _url: str, *, subscription, **_kwargs):
        self.subscriptions.append(subscription)
        item = subscription["subscriptions"][0]
        filters = json.loads(item["filters"])
        symbol = str(filters["symbol"])
        asset = symbol.split("/", 1)[0].upper()
        yield {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": int(NOW.timestamp() * 1000),
            "payload": {
                "symbol": symbol,
                "window_s": 60,
                "data": [
                    {
                        "timestamp": int(NOW.timestamp() * 1000),
                        "value": "60000",
                        "full_accuracy_value": FULL_ACCURACY_BY_ASSET[asset],
                    }
                ],
            },
        }
        while not self.stop_event.is_set():
            await asyncio.sleep(0.001)


def test_chainlink_run_fans_out_to_four_independent_asset_subscriptions() -> None:
    async def exercise() -> tuple[ChainlinkTwapCollector, FanoutTransport]:
        stop_event = asyncio.Event()
        transport = FanoutTransport(stop_event)
        collector = ChainlinkTwapCollector(transport, Clock())
        task = asyncio.create_task(collector.run(stop_event))
        try:
            async with asyncio.timeout(1):
                while collector.status().parse_success_count < 4:
                    await asyncio.sleep(0.001)
        finally:
            stop_event.set()
        await asyncio.wait_for(task, timeout=1)
        return collector, transport

    collector, transport = asyncio.run(exercise())
    status = collector.status().as_dict()
    assert len(transport.subscriptions) == 4
    assert {
        item["subscriptions"][0]["filters"] for item in transport.subscriptions
    } == {f'{{"symbol":"{asset.value.lower()}/usd"}}' for asset in Asset}
    assert status["parse_success_count"] == 4
    assert status["history_size_by_asset"] == {asset.value: 1 for asset in Asset}
    assert set(status["per_asset"]) == {asset.value for asset in Asset}
    assert all(
        status["per_asset"][asset.value]["parse_success_count"] == 1 for asset in Asset
    )


def test_asset_hint_isolates_malformed_feed_and_history_bound() -> None:
    collector = ChainlinkTwapCollector(FanoutTransport(asyncio.Event()), Clock(), history_limit=2)
    valid = {
        "topic": "crypto_prices_twap_sixty",
        "type": "update",
        "timestamp": int(NOW.timestamp() * 1000),
        "payload": {
            "symbol": "btc/usd",
            "window_s": 60,
            "data": [
                {
                    "timestamp": int(NOW.timestamp() * 1000),
                    "value": "60000",
                    "full_accuracy_value": "60000000000000000000000",
                }
            ],
        },
    }
    valid_payload = valid["payload"]
    assert isinstance(valid_payload, dict)
    assert (
        collector.handle_message(
            {**valid, "payload": {**valid_payload, "symbol": "eth/usd"}},
            asset_hint=Asset.ETH,
        )
        == 1
    )
    assert (
        collector.handle_message(
            {"topic": "crypto_prices_twap_sixty", "type": "update", "payload": {}},
            asset_hint=Asset.SOL,
        )
        == 0
    )
    assert collector.status().history_size_by_asset["ETH"] == 1
    assert collector.status().history_size_by_asset["SOL"] == 0
    for index in range(3):
        payload = {
            **valid,
            "timestamp": int(NOW.timestamp() * 1000) + index + 1,
            "payload": {
                **valid_payload,
                "data": [
                    {
                        **valid_payload["data"][0],
                        "timestamp": int(NOW.timestamp() * 1000) + index + 1,
                        "full_accuracy_value": str((60000 + index) * 10**18),
                    }
                ],
            },
        }
        collector.handle_message(payload, asset_hint=Asset.BTC)
    assert collector.status().history_size_by_asset["BTC"] == 2


def test_asset_hint_does_not_relabel_returned_symbol() -> None:
    collector = ChainlinkTwapCollector(FanoutTransport(asyncio.Event()), Clock())
    parsed = collector.handle_message(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": int(NOW.timestamp() * 1000),
            "payload": {
                "symbol": "eth/usd",
                "window_s": 60,
                "data": [
                    {
                        "timestamp": int(NOW.timestamp() * 1000),
                        "value": "3000",
                        "full_accuracy_value": "3000000000000000000000",
                    }
                ],
            },
        },
        asset_hint=Asset.BTC,
    )

    status = collector.status().as_dict()
    assert parsed == 1
    assert status["history_size_by_asset"]["BTC"] == 0
    assert status["history_size_by_asset"]["ETH"] == 1
    assert status["per_asset"]["BTC"]["filter_mismatch_count"] == 1
    assert status["per_asset"]["BTC"]["last_filter_status"] == "FILTER_MISMATCH"
    assert status["per_asset"]["BTC"]["last_returned_symbol"] == "ETH/USD"
    assert status["per_asset"]["ETH"]["last_filter_status"] == "ROUTED_BY_RETURNED_SYMBOL"


def test_subscribe_snapshot_does_not_poison_live_parse_or_history() -> None:
    collector = ChainlinkTwapCollector(FanoutTransport(asyncio.Event()), Clock())
    snapshot = {
        "topic": "crypto_prices_twap_sixty",
        "type": "subscribe",
        "timestamp": int(NOW.timestamp() * 1000),
        "payload": {
            "symbol": "btc/usd",
            "window_s": 60,
            "data": [
                {
                    "timestamp": int(NOW.timestamp() * 1000) - index,
                    "value": "60000",
                    "full_accuracy_value": "60000000000000000000000",
                }
                for index in range(58)
            ],
        },
    }

    assert collector.handle_message(snapshot, asset_hint=Asset.BTC) == 0
    status = collector.status().as_dict()
    assert status["parse_failure_count"] == 0
    assert status["parse_success_count"] == 0
    assert status["subscription_snapshot_count"] == 1
    assert status["history_size_by_asset"]["BTC"] == 0
    assert status["last_message_at"] is None
    assert status["per_asset"]["BTC"]["subscription_snapshot_count"] == 1
    assert status["per_asset"]["BTC"]["last_frame_class"] == "SUBSCRIPTION_SNAPSHOT"


def test_direct_live_update_creates_history_and_last_message() -> None:
    collector = ChainlinkTwapCollector(FanoutTransport(asyncio.Event()), Clock())
    update = {
        "topic": "crypto_prices_twap_sixty",
        "type": "update",
        "timestamp": int(NOW.timestamp() * 1000),
        "payload": {
            "symbol": "xrp/usd",
            "window_s": 60,
            "timestamp": int(NOW.timestamp() * 1000),
            "value": "3",
            "full_accuracy_value": "3000000000000000000",
        },
    }

    assert collector.handle_message(update, asset_hint=Asset.XRP) == 1
    status = collector.status().as_dict()
    assert status["parse_failure_count"] == 0
    assert status["parse_success_count"] == 1
    assert status["history_size_by_asset"]["XRP"] == 1
    assert status["per_asset"]["XRP"]["last_message_at"] == NOW.isoformat()
    assert status["per_asset"]["XRP"]["last_frame_class"] == "LIVE_UPDATE"
