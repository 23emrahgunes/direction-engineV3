"""Read-only V3.5 fee and live-book simulation probe."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal
from time import perf_counter

from direction_engine_v3.adapters.polymarket import (
    CLOB_BOOK_URL,
    CLOB_MARKETS_URL,
    parse_clob_book,
    parse_fee_schedule,
    parse_gamma_market_discovery,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon, OrderSide, OutcomeSide
from direction_engine_v3.market_data import MarketBucket, SystemClock, epoch_slug, window_containing
from direction_engine_v3.pricing import LiquidityRole, PricingPolicy, simulate_depth

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
    slug = epoch_slug(window)
    async with PublicTransport(timeout_seconds=15) as transport:
        event = _object(
            await transport.get_json(_EVENT_URL.format(slug=slug)), "Gamma event"
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
        up_token = next(
            token.token_id
            for token in discovery.market.tokens
            if token.outcome is OutcomeSide.UP
        )
        started = perf_counter()
        raw_book = await transport.get_json(CLOB_BOOK_URL, params={"token_id": up_token})
        book_latency_ms = (perf_counter() - started) * 1_000
        book_recv = clock.utc_now()
        book = parse_clob_book(
            raw_book,
            recv_ts=book_recv,
            normalized_ts=clock.utc_now(),
            recv_monotonic_ns=clock.monotonic_ns(),
        )
        started = perf_counter()
        raw_market_info = await transport.get_json(
            f"{CLOB_MARKETS_URL}/{discovery.market.condition_id}"
        )
        fee_latency_ms = (perf_counter() - started) * 1_000
        fee_recv = clock.utc_now()
        schedule = parse_fee_schedule(
            raw_market_info,
            condition_id=discovery.market.condition_id,
            recv_ts=fee_recv,
            normalized_ts=clock.utc_now(),
            recv_monotonic_ns=clock.monotonic_ns(),
        )
        simulation = simulate_depth(
            book,
            schedule,
            side=OrderSide.BUY,
            requested_quantity=book.minimum_order_size,
            limit_price=Decimal("1"),
            role=LiquidityRole.TAKER,
            observed_at=clock.utc_now(),
            policy=PricingPolicy(
                max_book_age=timedelta(seconds=30),
                max_fee_age=timedelta(seconds=30),
                slippage_buffer_bps=Decimal("10"),
                fee_buffer_bps=Decimal("500"),
            ),
        )
    if simulation.filled_quantity == 0:
        raise RuntimeError("live UP book had no executable ask depth")
    print(
        json.dumps(
            {
                "all_in_cost_per_share": str(simulation.all_in_cost_per_share),
                "book_latency_ms": round(book_latency_ms, 2),
                "condition_id": simulation.condition_id,
                "executable_depth": str(simulation.executable_depth),
                "fee_latency_ms": round(fee_latency_ms, 2),
                "fee_rate": str(schedule.rate),
                "fee_usdc": str(simulation.total_fee_usdc),
                "fill_fraction": str(simulation.fill_fraction),
                "requested_quantity": str(simulation.requested_quantity),
                "token_id": simulation.token_id,
                "vwap": str(simulation.vwap),
                "worst_price": str(simulation.worst_price),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
