"""One-shot read-only market-data pipeline diagnostic."""

import argparse
import asyncio
import json

from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import MarketBucket, SystemClock
from direction_engine_v3.shadow.daemon import PublicShadowDataClient


async def probe(*, asset: Asset, horizon: Horizon) -> dict[str, object]:
    clock = SystemClock()
    bucket = MarketBucket(asset, horizon)
    async with PublicTransport(timeout_seconds=15.0) as transport:
        state = await PublicShadowDataClient(transport, clock).collect_bucket(
            bucket, now=clock.utc_now()
        )
    return {
        "asset": asset.value,
        "horizon": horizon.value,
        "observed_at": state.observed_at.isoformat(),
        "market_id": state.discovery.market.market_id if state.discovery else None,
        "condition_id": state.discovery.market.condition_id if state.discovery else None,
        "ptb_status": state.ptb_status,
        "ptb_reason": state.ptb_reason,
        "stages": [stage.as_dict() for stage in state.pipeline_stages],
        "real_order_submission": False,
        "label": "PAPER / SHADOW — NO REAL ORDER",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", choices=[item.value for item in Asset], default="BTC")
    parser.add_argument("--horizon", choices=[item.value for item in Horizon], default="5m")
    args = parser.parse_args()
    result = asyncio.run(probe(asset=Asset(args.asset), horizon=Horizon(args.horizon)))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
