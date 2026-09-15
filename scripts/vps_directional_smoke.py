"""Read-only V3.7 proof that live identity without a model/PTB abstains."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

from direction_engine_v3.adapters.polymarket import parse_gamma_market_discovery
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import MarketBucket, SystemClock, epoch_slug, window_containing
from direction_engine_v3.strategies.directional import DirectionalPolicy, assess_directional_edge

_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{name} was not an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RuntimeError(f"{name} was not an array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeError(f"{name} was not a non-empty string")
    return value


async def main() -> None:
    clock = SystemClock()
    bucket = MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES)
    window = window_containing(bucket, clock.utc_now())
    async with PublicTransport(timeout_seconds=15) as transport:
        event = _object(
            await transport.get_json(_EVENT_URL.format(slug=epoch_slug(window))),
            "Gamma event",
        )
    markets = _sequence(event.get("markets"), "Gamma event markets")
    if len(markets) != 1:
        raise RuntimeError("canonical event must contain one market")
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
    assessment = assess_directional_edge(
        discovery.market,
        None,
        None,
        None,
        None,
        None,
        None,
        observed_at=recv_ts,
        policy=DirectionalPolicy(
            minimum_net_edge=Decimal("0.03"),
            uncertainty_buffer=Decimal("0.01"),
            minimum_signal_stability=Decimal("0.70"),
            maximum_flip_rate=Decimal("0.20"),
            max_forecast_age=timedelta(seconds=2),
        ),
    )
    if assessment.reason != "OFFICIAL_PTB_UNAVAILABLE" or assessment.candidate is not None:
        raise RuntimeError("missing trading-critical state did not fail closed")
    print(
        json.dumps(
            {
                "action": assessment.action,
                "candidate": None,
                "condition_id": discovery.market.condition_id,
                "reason": assessment.reason,
                "scope": f"{bucket.asset.value}-{bucket.horizon.value}",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
