"""Read-only V3.4 validation of all twelve current public market buckets."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

from direction_engine_v3.adapters.polymarket import parse_gamma_market_discovery
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import (
    SUPPORTED_MARKET_BUCKETS,
    SystemClock,
    epoch_slug,
    require_canonical_discovery,
    window_containing,
)

_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
_ASSET_NAME = {
    Asset.BTC: "bitcoin",
    Asset.ETH: "ethereum",
    Asset.SOL: "solana",
    Asset.XRP: "xrp",
}
_MONTH_NAME = (
    "",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{name} was not an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeError(f"{name} was not a non-empty string")
    return value


def _hourly_slug(asset: Asset, start: datetime) -> str:
    eastern = start.astimezone(ZoneInfo("America/New_York"))
    hour = eastern.hour % 12 or 12
    meridiem = "am" if eastern.hour < 12 else "pm"
    return (
        f"{_ASSET_NAME[asset]}-up-or-down-{_MONTH_NAME[eastern.month]}-"
        f"{eastern.day}-{eastern.year}-{hour}{meridiem}-et"
    )


async def main() -> None:
    clock = SystemClock()
    observed_at = clock.utc_now()
    results: dict[str, object] = {}
    async with PublicTransport(timeout_seconds=15) as transport:
        for bucket in SUPPORTED_MARKET_BUCKETS:
            window = window_containing(bucket, observed_at)
            slug = (
                _hourly_slug(bucket.asset, window.start)
                if bucket.horizon is Horizon.ONE_HOUR
                else epoch_slug(window)
            )
            started = perf_counter()
            raw_event = await transport.get_json(_EVENT_URL.format(slug=slug))
            latency_ms = (perf_counter() - started) * 1_000
            event = _object(raw_event, "Gamma event")
            markets = event.get("markets")
            if isinstance(markets, (str, bytes)) or not isinstance(markets, Sequence):
                raise RuntimeError("Gamma event markets was not an array")
            if len(markets) != 1:
                raise RuntimeError("canonical Gamma event must contain exactly one market")
            recv_ts = clock.utc_now()
            discovery = parse_gamma_market_discovery(
                markets[0],
                event_id=_text(event.get("id"), "Gamma event ID"),
                asset=bucket.asset,
                horizon=bucket.horizon,
                recv_ts=recv_ts,
                normalized_ts=clock.utc_now(),
                recv_monotonic_ns=clock.monotonic_ns(),
            )
            require_canonical_discovery(discovery, window, expected_slug=slug)
            key = f"{bucket.asset.value}-{bucket.horizon.value}"
            results[key] = {
                "condition_id": discovery.market.condition_id,
                "latency_ms": round(latency_ms, 2),
                "reference_method": discovery.settlement.method,
                "reference_source": discovery.market.settlement_source,
                "slug": slug,
                "tte_seconds": round(window.time_to_expiry(recv_ts).total_seconds(), 3),
            }
    if len(results) != 12:
        raise RuntimeError("public validation did not accept all twelve market buckets")
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
