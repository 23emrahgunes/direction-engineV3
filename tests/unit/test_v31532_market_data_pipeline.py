import asyncio
from datetime import UTC, datetime

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.market_data import MarketBucket
from direction_engine_v3.market_data.official_runtime import PriceToBeatResolution
from direction_engine_v3.shadow.daemon import PublicShadowDataClient

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class Clock:
    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        return 1


class OfficialOnly:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, discovery, *, observed_at):
        del discovery, observed_at
        self.calls += 1
        return PriceToBeatResolution(None, None, "PTB_UNAVAILABLE", "BOUNDARY_HISTORY_MISSING")

    def chainlink_status(self):
        return _Status()

    def binance_hourly_status(self):
        return _Status()


class _Status:
    def as_dict(self) -> dict[str, object]:
        return {"status": "WAITING"}


class Transport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get_json(self, url: str, *, params=None):
        del params
        self.urls.append(url)
        if "gamma-api" in url:
            return {
                "id": "event-1",
                "markets": [
                    {
                        "id": "market-1",
                        "conditionId": "condition-1",
                        "question": "Bitcoin Up or Down",
                        "slug": "btc-updown-5m",
                        "outcomes": '["Down", "Up"]',
                        "clobTokenIds": '["down-token", "up-token"]',
                        "startDate": "2026-09-17T11:55:00Z",
                        "eventStartTime": "2026-09-17T12:00:00Z",
                        "endDate": "2026-09-17T12:05:00Z",
                        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
                        "description": "Resolve from Chainlink TWAP.",
                        "cryptoMarketConfigId": "btc-5m-twap-60",
                        "cryptoMarketConfig": {
                            "id": "btc-5m-twap-60",
                            "asset": "btc",
                            "duration": "5m",
                            "twapEnabled": True,
                            "twapLookbackSeconds": 60,
                        },
                        "version": "v1",
                        "updatedAt": "2026-09-17T11:59:59Z",
                    }
                ],
            }
        if "fee-rate" in url:
            return {"unexpected": True}
        return {}


def test_gamma_identity_and_official_stage_survive_fee_schema_failure() -> None:
    transport = Transport()
    official = OfficialOnly()
    client = PublicShadowDataClient(transport, Clock(), official_ptb=official)
    state = asyncio.run(
        client.collect_bucket(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES), now=NOW)
    )

    assert state.discovery is not None
    assert state.discovery.market.condition_id == "condition-1"
    assert official.calls == 1
    assert state.official_reference is None
    fee_stages = [stage for stage in state.pipeline_stages if stage.stage == "FEE_UP_PARSE"]
    assert fee_stages and fee_stages[0].reason == "MARKET_DATA_SCHEMA_ERROR:FEE_PARSE"
    assert state.unavailable_reason == "MARKET_DATA_SCHEMA_ERROR:CLOB_UP_BOOK_PARSE"
