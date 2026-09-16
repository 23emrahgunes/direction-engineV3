"""Read-only Binance 1h official-candle probe for V3.15.3.1 acceptance."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.diagnostics.official_source_audit import audit_sources
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import MarketDiscovery
from direction_engine_v3.market_data.clock import SystemClock
from direction_engine_v3.market_data.official import official_reference_from_binance_hourly_candle
from direction_engine_v3.market_data.official_runtime import _parse_binance_hourly_candle

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


async def probe_binance_hourly(*, asset: Asset = Asset.BTC) -> dict[str, object]:
    """Validate the current 1h market's exact Binance candle open path."""

    clock = SystemClock()
    result: dict[str, object] = {
        "generated_at": clock.utc_now().isoformat(),
        "asset": asset.value,
        "horizon": Horizon.ONE_HOUR.value,
        "status": "NOT_STARTED",
        "real_order_submission": False,
    }
    source_audit = await audit_sources(asset=asset)
    audited_markets = source_audit.get("markets")
    if not isinstance(audited_markets, list):
        return result | {"status": "ERROR", "error": "SOURCE_AUDIT_MARKETS_UNAVAILABLE"}
    one_hour = next(
        (
            item
            for item in audited_markets
            if isinstance(item, dict) and item.get("horizon") == Horizon.ONE_HOUR.value
        ),
        None,
    )
    if one_hour is None or one_hour.get("status") != "OK":
        return result | {"status": "ERROR", "error": "CURRENT_1H_MARKET_UNAVAILABLE"}

    window_start = datetime.fromisoformat(str(one_hour["window_start"]))
    window_end = datetime.fromisoformat(str(one_hour["window_end"]))
    params = {
        "symbol": f"{asset.value}USDT",
        "interval": "1h",
        "startTime": str(int(window_start.timestamp() * 1000)),
        "endTime": str(int(window_end.timestamp() * 1000)),
        "limit": "1",
    }
    async with PublicTransport(timeout_seconds=15.0) as transport:
        try:
            raw = await transport.get_json(_BINANCE_KLINES_URL, params=params)
            candle = _parse_binance_hourly_candle(raw, asset=asset, clock=clock)
            discovery = await _discovery_from_audit(asset=asset)
            reference = official_reference_from_binance_hourly_candle(
                discovery.market,
                candle,
                is_price_to_beat=True,
            )
            result |= {
                "status": "OK",
                "http_status": "OK",
                "market_window_start": window_start.isoformat(),
                "candle_open_time": candle.open_time.isoformat(),
                "candle_open_matches_window": candle.open_time == window_start,
                "open_price": str(candle.open_price),
                "official_reference_created": True,
                "official_reference_value": str(reference.value),
                "official_reference_source": reference.source,
            }
        except Exception as exc:
            result |= {
                "status": "ERROR",
                "http_status": "ERROR",
                "market_window_start": window_start.isoformat(),
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
    return result


async def _discovery_from_audit(*, asset: Asset) -> MarketDiscovery:
    # Reuse the production collector path for final contract validation.
    from direction_engine_v3.adapters.polymarket import parse_gamma_market_discovery
    from direction_engine_v3.diagnostics.official_source_audit import _EVENT_URL, _slug
    from direction_engine_v3.market_data import MarketBucket, window_containing

    clock = SystemClock()
    bucket = MarketBucket(asset, Horizon.ONE_HOUR)
    window = window_containing(bucket, clock.utc_now())
    async with PublicTransport(timeout_seconds=15.0) as transport:
        raw = await transport.get_json(_EVENT_URL.format(slug=_slug(bucket, window.start)))
    if not isinstance(raw, dict) or not isinstance(raw.get("markets"), list):
        raise RuntimeError("Gamma event did not contain markets")
    markets = raw["markets"]
    if len(markets) != 1:
        raise RuntimeError("Gamma event did not contain exactly one market")
    return parse_gamma_market_discovery(
        markets[0],
        event_id=str(raw.get("id")),
        asset=asset,
        horizon=Horizon.ONE_HOUR,
        recv_ts=clock.utc_now(),
        normalized_ts=clock.utc_now(),
        recv_monotonic_ns=clock.monotonic_ns(),
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
    result = asyncio.run(probe_binance_hourly(asset=Asset(args.asset)))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
