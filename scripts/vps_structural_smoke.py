"""Read-only V3.6 complete-set scanner probe against a current BTC 5m market."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

from direction_engine_v3.adapters.polymarket import (
    CLOB_BOOK_URL,
    CLOB_MARKETS_URL,
    parse_clob_book,
    parse_fee_schedule,
    parse_gamma_market_discovery,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon, OutcomeSide
from direction_engine_v3.market_data import MarketBucket, SystemClock, epoch_slug, window_containing
from direction_engine_v3.pricing import PricingPolicy
from direction_engine_v3.strategies.structural_arb import (
    StructuralAction,
    StructuralPolicy,
    scan_complete_set,
)

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
        token_ids = {token.outcome: token.token_id for token in discovery.market.tokens}

        async def fetch_book(outcome: OutcomeSide):
            raw = await transport.get_json(
                CLOB_BOOK_URL, params={"token_id": token_ids[outcome]}
            )
            received = clock.utc_now()
            return parse_clob_book(
                raw,
                recv_ts=received,
                normalized_ts=clock.utc_now(),
                recv_monotonic_ns=clock.monotonic_ns(),
            )

        up_book, down_book = await asyncio.gather(
            fetch_book(OutcomeSide.UP), fetch_book(OutcomeSide.DOWN)
        )
        raw_fee = await transport.get_json(
            f"{CLOB_MARKETS_URL}/{discovery.market.condition_id}"
        )
        fee_recv = clock.utc_now()
        fee = parse_fee_schedule(
            raw_fee,
            condition_id=discovery.market.condition_id,
            recv_ts=fee_recv,
            normalized_ts=clock.utc_now(),
            recv_monotonic_ns=clock.monotonic_ns(),
        )
        observed_at = clock.utc_now()
        policy = StructuralPolicy(
            target_shares=max(up_book.minimum_order_size, down_book.minimum_order_size),
            minimum_net_profit=Decimal("0.01"),
            cycle_buffer_bps=Decimal("10"),
            max_pair_skew=timedelta(seconds=5),
            pricing=PricingPolicy(
                max_book_age=timedelta(seconds=30),
                max_fee_age=timedelta(seconds=30),
                slippage_buffer_bps=Decimal("10"),
                fee_buffer_bps=Decimal("500"),
            ),
        )
        scans = {
            action.value: scan_complete_set(
                discovery.market,
                up_book,
                down_book,
                fee,
                fee,
                action=action,
                observed_at=observed_at,
                policy=policy,
            )
            for action in StructuralAction
        }
    output = {
        action: {
            "expected_net_profit": (
                None
                if scan.opportunity is None
                else str(scan.opportunity.expected_net_profit)
            ),
            "reason": scan.reason,
        }
        for action, scan in scans.items()
    }
    output["market"] = {
        "condition_id": discovery.market.condition_id,
        "down_best_ask": str(down_book.asks[0].price) if down_book.asks else None,
        "pair_source_skew_ms": abs(
            (up_book.lineage.source_ts - down_book.lineage.source_ts).total_seconds()
            * 1_000
        )
        if up_book.lineage.source_ts is not None and down_book.lineage.source_ts is not None
        else None,
        "target_shares": str(policy.target_shares),
        "up_best_ask": str(up_book.asks[0].price) if up_book.asks else None,
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
