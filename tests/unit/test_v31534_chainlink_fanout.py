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
