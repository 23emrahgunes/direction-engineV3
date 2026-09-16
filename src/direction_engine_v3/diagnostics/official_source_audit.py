"""Read-only Gamma metadata audit for current official source rules."""

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from direction_engine_v3.adapters.polymarket import parse_gamma_market_discovery
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import MarketBucket, epoch_slug, window_containing
from direction_engine_v3.market_data.clock import SystemClock

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


async def audit_sources(
    *,
    asset: Asset = Asset.BTC,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Retrieve and normalize current 5m/15m/1h Gamma source metadata."""

    clock = SystemClock()
    now = observed_at or clock.utc_now()
    rows: list[dict[str, object]] = []
    async with PublicTransport(timeout_seconds=15.0) as transport:
        for horizon in (Horizon.FIVE_MINUTES, Horizon.FIFTEEN_MINUTES, Horizon.ONE_HOUR):
            bucket = MarketBucket(asset, horizon)
            window = window_containing(bucket, now)
            slug = _slug(bucket, window.start)
            row: dict[str, object] = {
                "asset": asset.value,
                "horizon": horizon.value,
                "slug": slug,
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(),
                "status": "NOT_STARTED",
            }
            try:
                raw = await transport.get_json(_EVENT_URL.format(slug=slug))
                if not isinstance(raw, dict):
                    raise RuntimeError("Gamma event was not an object")
                markets = raw.get("markets")
                if not isinstance(markets, list) or len(markets) != 1:
                    raise RuntimeError("Gamma event did not contain exactly one market")
                recv_ts = clock.utc_now()
                discovery = parse_gamma_market_discovery(
                    markets[0],
                    event_id=str(raw.get("id")),
                    asset=asset,
                    horizon=horizon,
                    recv_ts=recv_ts,
                    normalized_ts=clock.utc_now(),
                    recv_monotonic_ns=clock.monotonic_ns(),
                )
                row |= {
                    "status": "OK",
                    "event_id": discovery.event_id,
                    "market_id": discovery.market.market_id,
                    "condition_id": discovery.market.condition_id,
                    "settlement_source": discovery.market.settlement_source,
                    "settlement_method": discovery.settlement.method.value,
                    "configuration_id": discovery.settlement.configuration_id,
                    "reference_period_seconds": discovery.settlement.reference_period_seconds,
                    "rules_text": discovery.settlement.rules,
                    "source_url_or_label": discovery.market.settlement_source,
                    "question": discovery.question,
                }
            except Exception as exc:
                row |= {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:500]}
            rows.append(row)
    return {
        "generated_at": clock.utc_now().isoformat(),
        "asset": asset.value,
        "observed_at": now.isoformat(),
        "markets": rows,
        "real_order_submission": False,
    }


def _slug(bucket: MarketBucket, start: datetime) -> str:
    if bucket.horizon is not Horizon.ONE_HOUR:
        return epoch_slug(window_containing(bucket, start))
    try:
        eastern = start.astimezone(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        eastern = start.astimezone(timezone(timedelta(hours=-4), "America/New_York"))
    hour = eastern.hour % 12 or 12
    meridiem = "am" if eastern.hour < 12 else "pm"
    return (
        f"{_ASSET_NAME[bucket.asset]}-up-or-down-{_MONTH_NAME[eastern.month]}-"
        f"{eastern.day}-{eastern.year}-{hour}{meridiem}-et"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--asset",
        choices=[asset.value for asset in Asset],
        default=Asset.BTC.value,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(audit_sources(asset=Asset(args.asset)))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
