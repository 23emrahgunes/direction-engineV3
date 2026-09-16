from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from direction_engine_v3.domain import Asset, Horizon, Market, MarketToken, OutcomeSide
from direction_engine_v3.market_data import (
    DataSource,
    EventLineage,
    FeeSchedule,
    MarketBucket,
    MarketDiscovery,
    PolymarketBook,
    PolymarketLevel,
    SettlementMetadata,
    SettlementMethod,
)
from direction_engine_v3.shadow.daemon import ShadowDaemon, ShadowMarketState, new_evidence_window
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_structural_pair_skew_abstains_without_killing_cycle(tmp_path: Path) -> None:
    daemon, paper, shadow = _daemon(tmp_path)
    first = _state(
        observed_at=NOW + timedelta(seconds=6),
        up_book=_book("up-token", source_offset=0),
        down_book=_book("down-token", source_offset=6),
    )
    second = _state(
        observed_at=NOW + timedelta(milliseconds=50),
        up_book=_book("up-token", ask="0.40"),
        down_book=_book("down-token", ask="0.40"),
    )

    result = daemon._evaluate_cycle("cycle", NOW, (first, second))

    assert result.markets_discovered == 2
    assert result.paper_trades == 1
    abstains = paper.abstains(strategy="STRUCTURAL_ARBITRAGE", limit=20)
    assert any(item.reason == "PAIRED_BOOK_SOURCE_SKEW" for item in abstains)
    structural_events = shadow.latest_events(event_type="STRUCTURAL_EVALUATION", limit=20)
    assert any(
        isinstance(item["payload"], dict)
        and item["payload"].get("reason") == "PAIRED_BOOK_SOURCE_SKEW"
        for item in structural_events
    )


def test_structural_stale_book_abstains_and_next_daemon_cycle_runs(tmp_path: Path) -> None:
    daemon, paper, shadow = _daemon(tmp_path)
    stale = _state(
        observed_at=NOW + timedelta(seconds=31),
        up_book=_book("up-token"),
        down_book=_book("down-token"),
    )

    first = daemon._evaluate_cycle("cycle-1", NOW, (stale,))
    second = daemon._evaluate_cycle("cycle-2", NOW + timedelta(seconds=1), (stale,))

    assert first.paper_trades == 0
    assert second.paper_trades == 0
    abstains = paper.abstains(strategy="STRUCTURAL_ARBITRAGE", limit=20)
    assert any(item.reason == "BOOK_STALE" for item in abstains)
    assert len(shadow.latest_events(event_type="STRUCTURAL_EVALUATION", limit=20)) >= 2


def test_unexpected_structural_runtime_error_is_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import direction_engine_v3.shadow.daemon as daemon_module

    daemon, _paper, _shadow = _daemon(tmp_path)

    def raise_runtime_error(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("programming defect")

    monkeypatch.setattr(daemon_module, "scan_complete_set", raise_runtime_error)
    with pytest.raises(RuntimeError, match="programming defect"):
        daemon._evaluate_structural(_state(), cycle_id="cycle")


def _daemon(tmp_path: Path) -> tuple[ShadowDaemon, SQLitePaperRepository, SQLiteShadowRepository]:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    daemon = ShadowDaemon(
        data_client=_NoopDataClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=new_evidence_window(
            aws_user_id="user",
            aws_account="account",
            aws_arn="arn:aws:iam::123:root",
            started_at=NOW,
            commit="abcdef123456",
        ),
        report_dir=tmp_path,
    )
    return daemon, paper, shadow


class _NoopDataClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        raise AssertionError("not used")


def _state(
    *,
    observed_at: datetime = NOW + timedelta(milliseconds=50),
    up_book: PolymarketBook | None = None,
    down_book: PolymarketBook | None = None,
) -> ShadowMarketState:
    return ShadowMarketState(
        bucket=MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES),
        discovery=_discovery(),
        up_book=up_book or _book("up-token", ask="0.40"),
        down_book=down_book or _book("down-token", ask="0.40"),
        fee_schedule=_fee(),
        proxy_reference=None,
        official_reference=None,
        observed_at=observed_at,
    )


def _discovery() -> MarketDiscovery:
    market = Market(
        "market-1",
        "condition-1",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        (
            MarketToken("up-token", OutcomeSide.UP),
            MarketToken("down-token", OutcomeSide.DOWN),
        ),
        NOW,
        NOW + timedelta(minutes=5),
        "https://data.chain.link/streams/btc-usd-twap-60s-streams",
    )
    return MarketDiscovery(
        "event-1",
        "btc-updown-5m-1",
        "BTC Up or Down",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            market.settlement_source,
            "rules",
            SettlementMethod.CHAINLINK_TWAP,
            "btc-5m-twap-60",
            Asset.BTC,
            Horizon.FIVE_MINUTES,
            60,
            "1",
            _lineage(DataSource.POLYMARKET_GAMMA),
        ),
    )


def _book(
    token_id: str,
    *,
    bid: str = "0.35",
    ask: str = "0.40",
    quantity: str = "10",
    source_offset: int = 0,
) -> PolymarketBook:
    return PolymarketBook(
        condition_id="condition-1",
        token_id=token_id,
        bids=(PolymarketLevel(Decimal(bid), Decimal(quantity)),),
        asks=(PolymarketLevel(Decimal(ask), Decimal(quantity)),),
        checksum=f"{token_id}-{source_offset}",
        minimum_order_size=Decimal("5"),
        tick_size=Decimal("0.01"),
        lineage=_lineage(DataSource.POLYMARKET_CLOB, offset=source_offset),
    )


def _fee() -> FeeSchedule:
    return FeeSchedule(
        condition_id="condition-1",
        maker_base_bps=Decimal("0"),
        taker_base_bps=Decimal("1000"),
        rate=Decimal("0.07"),
        exponent=Decimal("1"),
        lineage=replace(_lineage(DataSource.POLYMARKET_CLOB), source_ts=None),
    )


def _lineage(source: DataSource, *, offset: int = 0) -> EventLineage:
    timestamp = NOW + timedelta(seconds=offset)
    return EventLineage(
        source=source,
        source_ts=timestamp,
        recv_ts=timestamp,
        normalized_ts=timestamp,
        recv_monotonic_ns=100 + offset,
    )
