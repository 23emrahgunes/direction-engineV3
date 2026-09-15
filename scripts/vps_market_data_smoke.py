"""Controlled credential-free V3.2 public-network acceptance probe."""

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from time import perf_counter

from direction_engine_v3.adapters.binance import parse_aggregate_trade, stream_url
from direction_engine_v3.adapters.chainlink import (
    RTDS_HEARTBEAT_SECONDS,
    RTDS_WS_URL,
    parse_twap,
    twap_subscription,
)
from direction_engine_v3.adapters.polymarket import GAMMA_MARKETS_URL
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data import SystemClock, utc_from_milliseconds


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError("public smoke response was not an object")
    return value


def _clock_skew_ms(now: datetime, source_ms: int) -> float:
    source_time = utc_from_milliseconds(source_ms)
    return (now - source_time).total_seconds() * 1_000


async def _rest_probe(
    transport: PublicTransport, url: str, *, params: Mapping[str, str] | None = None
) -> tuple[object, float]:
    started = perf_counter()
    payload = await transport.get_json(url, params=params)
    return payload, (perf_counter() - started) * 1_000


async def _binance_trade_probe(transport: PublicTransport) -> dict[str, object]:
    clock = SystemClock()
    async with asyncio.timeout(15):
        async for raw in transport.websocket_json(stream_url(Asset.BTC, "aggTrade")):
            recv_ts = clock.utc_now()
            trade = parse_aggregate_trade(
                raw,
                asset=Asset.BTC,
                recv_ts=recv_ts,
                normalized_ts=clock.utc_now(),
                recv_monotonic_ns=clock.monotonic_ns(),
            )
            source_ts = trade.lineage.source_ts
            if source_ts is None:
                raise RuntimeError("Binance trade omitted source timestamp")
            return {
                "asset": trade.asset,
                "source_age_ms": (recv_ts - source_ts).total_seconds() * 1_000,
            }
    raise RuntimeError("Binance public stream closed before a trade arrived")


async def _chainlink_probe(transport: PublicTransport) -> dict[str, object]:
    clock = SystemClock()
    subscription = twap_subscription(Asset.BTC, window_seconds=30)
    async with asyncio.timeout(20):
        async for raw in transport.websocket_json(
            RTDS_WS_URL,
            subscription=subscription,
            text_heartbeat_seconds=RTDS_HEARTBEAT_SECONDS,
        ):
            payload = _object(raw)
            if payload.get("topic") != "crypto_prices_twap_thirty":
                continue
            recv_ts = clock.utc_now()
            twap = parse_twap(
                payload,
                asset=Asset.BTC,
                window_seconds=30,
                recv_ts=recv_ts,
                normalized_ts=clock.utc_now(),
                recv_monotonic_ns=clock.monotonic_ns(),
            )
            source_ts = twap.lineage.source_ts
            if source_ts is None:
                raise RuntimeError("Chainlink TWAP omitted source timestamp")
            return {
                "asset": twap.asset,
                "window_seconds": twap.window_seconds,
                "source_age_ms": (recv_ts - source_ts).total_seconds() * 1_000,
                "publisher_age_ms": (recv_ts - twap.publisher_ts).total_seconds() * 1_000,
            }
    raise RuntimeError("Chainlink RTDS closed before a matching update arrived")


async def main() -> None:
    clock = SystemClock()
    results: dict[str, object] = {}
    async with PublicTransport(timeout_seconds=15) as transport:
        binance_time, latency = await _rest_probe(
            transport, "https://api.binance.com/api/v3/time"
        )
        binance_ms = int(_object(binance_time)["serverTime"])
        results["binance_rest"] = {
            "latency_ms": latency,
            "clock_skew_ms": _clock_skew_ms(clock.utc_now(), binance_ms),
        }
        polymarket_time, latency = await _rest_probe(
            transport, "https://clob.polymarket.com/time"
        )
        polymarket_ms = int(Decimal(str(polymarket_time)))
        if polymarket_ms < 1_000_000_000_000:
            polymarket_ms *= 1_000
        results["polymarket_clob_rest"] = {
            "latency_ms": latency,
            "clock_skew_ms": _clock_skew_ms(clock.utc_now(), polymarket_ms),
        }
        gamma, latency = await _rest_probe(
            transport,
            GAMMA_MARKETS_URL,
            params={"active": "true", "closed": "false", "limit": "1"},
        )
        if not isinstance(gamma, list) or not gamma:
            raise RuntimeError("Gamma returned no active public market")
        results["polymarket_gamma_rest"] = {"latency_ms": latency, "records": len(gamma)}
        results["binance_trade"] = await _binance_trade_probe(transport)
        results["chainlink_rtds"] = await _chainlink_probe(transport)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
