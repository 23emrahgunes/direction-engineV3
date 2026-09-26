import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    Market,
    MarketToken,
    OfficialReference,
    OutcomeSide,
    ProbabilityForecast,
    ProxyReference,
    StrategyCandidate,
    StrategyKind,
)
from direction_engine_v3.features import ExternalDirectionalSnapshot, build_directional_features
from direction_engine_v3.market_data import (
    OFFICIAL_REFERENCE_SOURCES,
    DataSource,
    EventLineage,
    FeeSchedule,
    MarketBucket,
    MarketDataError,
    MarketDiscovery,
    PolymarketBook,
    PolymarketLevel,
    PriceToBeatRecord,
    SettlementMetadata,
    SettlementMethod,
)
from direction_engine_v3.shadow.daemon import (
    ShadowDaemon,
    ShadowMarketState,
    _paper_directional_entry_brake,
    new_evidence_window,
)
from direction_engine_v3.shadow.storage import ShadowStorageBusy, SQLiteShadowRepository
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)


class StaticClock:
    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        return 1


class SequenceClock:
    def __init__(self, values: tuple[datetime, ...]) -> None:
        self._values = values
        self._index = 0

    def utc_now(self) -> datetime:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value

    def monotonic_ns(self) -> int:
        return self._index


class FixtureClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        if bucket != MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES):
            return ShadowMarketState(bucket, None, None, None, None, None, None, now, "fixture")
        return _market_state(bucket)


class RecordingClient:
    def __init__(self) -> None:
        self.selection_times: list[datetime] = []

    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        self.selection_times.append(now)
        return ShadowMarketState(bucket, None, None, None, None, None, None, now, "fixture")


class OneBucketMarketDataFailureClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        if bucket == MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES):
            raise MarketDataError("temporary public data timeout")
        if bucket == MarketBucket(Asset.ETH, Horizon.FIVE_MINUTES):
            return _market_state(bucket)
        return ShadowMarketState(bucket, None, None, None, None, None, None, now, "fixture")


class ProgrammingFailureClient:
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        raise RuntimeError("programming defect")


class HangingBucketClient:
    def __init__(self) -> None:
        self.cancelled = 0

    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        if bucket == MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return ShadowMarketState(bucket, None, None, None, None, None, None, now, "fixture")


class BusyShadowEventRepository:
    def append_event(self, **_: object) -> None:
        raise ShadowStorageBusy("STORAGE_BUSY:shadow_evidence_write")


class SettlementProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self) -> dict[str, object]:
        self.calls += 1
        return {
            "settlement_checked": 0,
            "settlement_pending": 0,
            "settlement_completed": 0,
            "settlement_blocked": 0,
            "last_settlement_check_at": NOW.isoformat(),
            "last_settlement_error": None,
        }


class HangingSettlementProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def run_once(self) -> dict[str, object]:
        self.calls += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {}


def test_shadow_daemon_records_evaluations_abstains_and_paper_trade(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    first = asyncio.run(daemon.run_once())
    second = asyncio.run(daemon.run_once())

    assert first.markets_discovered == 1
    assert first.book_observations == 2
    assert first.strategy_evaluations >= 3
    assert first.abstain_records >= 11
    assert first.paper_trades == 2
    assert second.paper_trades == 1
    trades = paper.trades()
    assert len(trades) == 2
    assert any(
        item.reason == "DIRECTIONAL_POSITION_ALREADY_OPEN" for item in paper.abstains()
    )
    directional = next(item for item in trades if item.strategy == "DIRECTIONAL_EDGE")
    assert directional.label == "PAPER / SHADOW — NO REAL ORDER"
    assert directional.payload["model_version"] == "PAPER_RESEARCH_BASELINE"
    assert directional.payload["real_order_submission"] is False
    assert paper.summary()["open_positions"] == 2
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1


def test_shadow_event_storage_busy_is_visible_in_stderr(tmp_path, capsys) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=BusyShadowEventRepository(),
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    appended = daemon._append_shadow_event(
        event_id=f"{window.window_id}:event",
        window_id=window.window_id,
        event_type="REAL_SHADOW_CYCLE",
        bucket_key=None,
        payload={"ok": True},
        observed_at=NOW,
    )

    captured = capsys.readouterr()
    assert appended is False
    assert "SHADOW_EVENT_STORAGE_BUSY" in captured.err
    assert "REAL_SHADOW_CYCLE" in captured.err
    assert window.window_id in captured.err


def test_negative_paper_capital_abstains_without_crashing_and_keeps_settlement_scan(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    paper.save_trade_snapshot(
        trade_id="existing-filled-over-budget",
        decision_id="decision:existing",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-existing",
        side="UP",
        status="OPEN",
        observed_at=NOW - timedelta(minutes=1),
        payload={
            "condition_id": "condition-existing",
            "market_id": "market-existing",
            "stake": "1200",
            "cost_basis_usdc": "1200",
            "shares": "1200",
            "window_end": (NOW + timedelta(minutes=4)).isoformat(),
            "fill_status": "FILLED",
            "real_order_submission": False,
        },
    )
    probe = SettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert probe.calls == 1
    assert result.markets_discovered == 1
    capital_abstain = next(
        item for item in paper.abstains() if item.reason == "PAPER_CAPITAL_DEFICIT"
    )
    assert capital_abstain.payload["raw_available_capital"] == "-1160.00"
    assert capital_abstain.payload["spendable_capital"] == "0"
    directional_trades = paper.trades(strategy="DIRECTIONAL_EDGE", limit=100)
    assert len(directional_trades) == 1
    structural_trades = paper.trades(strategy="STRUCTURAL_ARBITRAGE", limit=100)
    assert structural_trades == ()
    assert any(
        item.reason == "PAPER_CAPITAL_DEFICIT" and item.strategy == "STRUCTURAL_ARBITRAGE"
        for item in paper.abstains()
    )
    summary = paper.summary(now=NOW)
    assert summary["raw_available_capital"] == "-1160.00"
    assert summary["spendable_capital"] == "0"
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1


def test_settlement_scan_timeout_records_evidence_and_cycle_continues(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "direction_engine_v3.shadow.daemon._PAPER_SETTLEMENT_SCAN_TIMEOUT_SECONDS",
        0.01,
    )
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    probe = HangingSettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert probe.calls == 1
    assert probe.cancelled is True
    assert result.markets_discovered == 1
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1
    settlement = shadow.latest_events(event_type="PAPER_SETTLEMENT_SCAN", limit=1)[0]
    assert settlement["payload"]["status"] == "SETTLEMENT_SCAN_TIMEOUT"
    assert settlement["payload"]["reason"] == "PAPER_SETTLEMENT_SCAN_TIMEOUT"


def test_expected_bucket_collection_failure_does_not_stop_cycle_or_other_buckets(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    probe = SettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=OneBucketMarketDataFailureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert probe.calls == 1
    assert result.markets_discovered >= 1
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1
    assert any(
        item.reason == "MARKET_DATA_ERROR:COLLECT_BUCKET"
        for item in paper.abstains()
    )


def test_bucket_collection_timeout_records_bucket_failure_and_cycle_continues(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "direction_engine_v3.shadow.daemon._BUCKET_COLLECTION_TIMEOUT_SECONDS",
        0.01,
    )
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    client = HangingBucketClient()
    probe = SettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=client,
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert client.cancelled == 1
    assert result.markets_discovered == 0
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1
    bucket_event = shadow.latest_events(
        event_type="MARKET_DATA_PIPELINE",
        bucket_key="BTC-5m",
        limit=1,
    )[0]
    assert bucket_event["payload"]["unavailable_reason"] == "TRANSPORT_TIMEOUT:COLLECT_BUCKET"


def test_unexpected_bucket_collection_failure_still_raises_after_settlement_scan(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    probe = SettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=ProgrammingFailureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=StaticClock(),
        poll_seconds=1,
    )

    try:
        asyncio.run(daemon.run_once())
    except RuntimeError as exc:
        assert "programming defect" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("programming defect was swallowed")

    assert probe.calls == 1
    assert shadow.event_counts()["PAPER_SETTLEMENT_SCAN"] == 1
    assert shadow.event_counts().get("REAL_SHADOW_CYCLE", 0) == 0


def test_paper_drawdown_brake_blocks_new_directional_fill_without_stopping_cycle(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    paper.save_trade_snapshot(
        trade_id="settled-drawdown-loss",
        decision_id="decision:drawdown-loss",
        strategy="DIRECTIONAL_EDGE",
        asset="ETH",
        horizon="5m",
        condition_id="condition-drawdown-loss",
        side="UP",
        status="SETTLED",
        observed_at=NOW - timedelta(minutes=20),
        payload={
            "stake": "10",
            "cost_basis_usdc": "10",
            "realized_paper_pnl": "-10",
            "win_loss": "LOSS",
            "real_order_submission": False,
        },
    )
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    shadow.save_window_once(window_id=window.window_id, payload=window.as_dict(), started_at=NOW)
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    result = asyncio.run(daemon.run_once())

    assert result.markets_discovered == 1
    assert shadow.event_counts()["REAL_SHADOW_CYCLE"] == 1
    assert paper.trades(strategy="DIRECTIONAL_EDGE", status="OPEN") == ()
    abstain = next(
        item for item in paper.abstains() if item.reason == "PAPER_DRAWDOWN_BRAKE_ACTIVE"
    )
    assert abstain.payload["risk_brake_active"] is True
    assert abstain.payload["paper_current_equity"] == "30.00"


def test_paper_open_exposure_brake_blocks_new_directional_fill(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    paper.save_trade_snapshot(
        trade_id="open-exposure",
        decision_id="decision:open-exposure",
        strategy="DIRECTIONAL_EDGE",
        asset="SOL",
        horizon="5m",
        condition_id="condition-open-exposure",
        side="UP",
        status="OPEN",
        observed_at=NOW - timedelta(minutes=1),
        payload={
            "stake": "12",
            "cost_basis_usdc": "12",
            "window_end": (NOW + timedelta(minutes=5)).isoformat(),
            "fill_status": "FILLED",
            "real_order_submission": False,
        },
    )
    summary = paper.summary(now=NOW)
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    candidate = StrategyCandidate(
        "candidate-open-exposure",
        StrategyKind.DIRECTIONAL_EDGE,
        state.discovery.market.market_id,
        Decimal("1"),
        Decimal("0.05"),
        Decimal("0.5"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )
    forecast = ProbabilityForecast(
        state.discovery.market.market_id,
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.55"),
        Decimal("0.45"),
        "READY_MODEL",
        "READY_CALIBRATION",
        "features-v1",
        NOW,
        NOW,
    )

    brake = _paper_directional_entry_brake(
        summary,
        open_trades=paper.trades(strategy="DIRECTIONAL_EDGE", status="OPEN"),
        recent_trades=paper.trades(strategy="DIRECTIONAL_EDGE"),
        candidate=candidate,
        market=state.discovery.market,
        forecast=forecast,
    )

    assert brake.active is True
    assert "OPEN_EXPOSURE_LIMIT" in brake.reasons


def test_paper_open_position_count_brake_blocks_new_directional_fill(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    for index in range(4):
        paper.save_trade_snapshot(
            trade_id=f"open-{index}",
            decision_id=f"decision:open-{index}",
            strategy="DIRECTIONAL_EDGE",
            asset=("ETH", "SOL", "XRP", "ETH")[index],
            horizon="5m",
            condition_id=f"condition-open-{index}",
            side="UP",
            status="OPEN",
            observed_at=NOW - timedelta(minutes=index + 1),
            payload={
                "stake": "0.01",
                "cost_basis_usdc": "0.01",
                "window_end": (NOW + timedelta(minutes=5)).isoformat(),
                "fill_status": "FILLED",
                "real_order_submission": False,
            },
        )
    summary = paper.summary(now=NOW)
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    candidate = StrategyCandidate(
        "candidate-open-count",
        StrategyKind.DIRECTIONAL_EDGE,
        state.discovery.market.market_id,
        Decimal("1"),
        Decimal("0.05"),
        Decimal("0.5"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )
    forecast = ProbabilityForecast(
        state.discovery.market.market_id,
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.55"),
        Decimal("0.45"),
        "READY_MODEL",
        "READY_CALIBRATION",
        "features-v1",
        NOW,
        NOW,
    )

    brake = _paper_directional_entry_brake(
        summary,
        open_trades=paper.trades(strategy="DIRECTIONAL_EDGE", status="OPEN"),
        recent_trades=paper.trades(strategy="DIRECTIONAL_EDGE"),
        candidate=candidate,
        market=state.discovery.market,
        forecast=forecast,
    )

    assert brake.active is True
    assert "MAXIMUM_PAPER_POSITIONS" in brake.reasons


def test_paper_recent_poor_performance_brake_blocks_new_directional_fill(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    for index in range(10):
        paper.save_trade_snapshot(
            trade_id=f"recent-loss-{index}",
            decision_id=f"decision:recent-loss-{index}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=f"condition-recent-loss-{index}",
            side="UP",
            status="SETTLED",
            observed_at=NOW - timedelta(minutes=index + 1),
            payload={
                "stake": "0.10",
                "cost_basis_usdc": "0.10",
                "realized_paper_pnl": "-0.10",
                "win_loss": "LOSS",
                "real_order_submission": False,
            },
        )
    summary = paper.summary(now=NOW)
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    candidate = StrategyCandidate(
        "candidate-recent-losses",
        StrategyKind.DIRECTIONAL_EDGE,
        state.discovery.market.market_id,
        Decimal("1"),
        Decimal("0.05"),
        Decimal("0.5"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )
    forecast = ProbabilityForecast(
        state.discovery.market.market_id,
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.55"),
        Decimal("0.45"),
        "READY_MODEL",
        "READY_CALIBRATION",
        "features-v1",
        NOW,
        NOW,
    )

    brake = _paper_directional_entry_brake(
        summary,
        open_trades=paper.trades(strategy="DIRECTIONAL_EDGE", status="OPEN"),
        recent_trades=paper.trades(strategy="DIRECTIONAL_EDGE"),
        candidate=candidate,
        market=state.discovery.market,
        forecast=forecast,
    )

    assert brake.active is True
    assert "POOR_RECENT_PAPER_PERFORMANCE" in brake.reasons
    assert brake.recent_win_rate == Decimal("0")


def test_paper_baseline_stake_cap_blocks_large_research_baseline_candidate(
    tmp_path,
) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    summary = paper.summary(now=NOW)
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    candidate = StrategyCandidate(
        "candidate-large-baseline",
        StrategyKind.DIRECTIONAL_EDGE,
        state.discovery.market.market_id,
        Decimal("1.51"),
        Decimal("0.05"),
        Decimal("0.5"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )
    forecast = ProbabilityForecast(
        state.discovery.market.market_id,
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.55"),
        Decimal("0.45"),
        "PAPER_RESEARCH_BASELINE",
        "PAPER_RESEARCH_BASELINE_UNPROMOTABLE",
        "features-v1",
        NOW,
        NOW,
    )

    brake = _paper_directional_entry_brake(
        summary,
        open_trades=(),
        recent_trades=(),
        candidate=candidate,
        market=state.discovery.market,
        forecast=forecast,
    )

    assert brake.active is True
    assert brake.reason == "BASELINE_RESEARCH_STAKE_CAP"


def test_paper_entry_brake_allows_healthy_small_candidate(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    paper.initialize()
    summary = paper.summary(now=NOW)
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    candidate = StrategyCandidate(
        "candidate-healthy",
        StrategyKind.DIRECTIONAL_EDGE,
        state.discovery.market.market_id,
        Decimal("1.00"),
        Decimal("0.05"),
        Decimal("0.5"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )
    forecast = ProbabilityForecast(
        state.discovery.market.market_id,
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("0.55"),
        Decimal("0.45"),
        "READY_MODEL",
        "READY_CALIBRATION",
        "features-v1",
        NOW,
        NOW,
    )

    brake = _paper_directional_entry_brake(
        summary,
        open_trades=(),
        recent_trades=(),
        candidate=candidate,
        market=state.discovery.market,
        forecast=forecast,
    )

    assert brake.active is False
    assert brake.payload()["risk_brake_active"] is False


def test_shadow_daemon_uses_post_settlement_time_for_market_selection(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    client = RecordingClient()
    probe = SettlementProbe()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=client,
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        settlement_service=probe,
        clock=SequenceClock(
            (
                NOW,
                NOW + timedelta(minutes=5, seconds=1),
                NOW + timedelta(minutes=5, seconds=2),
                NOW + timedelta(minutes=5, seconds=3),
            )
        ),
        poll_seconds=1,
    )

    asyncio.run(daemon.run_once())

    assert probe.calls == 1
    assert set(client.selection_times) == {NOW + timedelta(minutes=5, seconds=2)}


def test_expired_bucket_abstains_without_strategy_or_paper_fill(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )
    state = _market_state(MarketBucket(Asset.BTC, Horizon.FIVE_MINUTES))
    expired_state = replace(
        state,
        observed_at=NOW + timedelta(minutes=5),
        unavailable_reason="MARKET_WINDOW_EXPIRED",
        feature_status="MARKET_WINDOW_EXPIRED",
        directional_features=None,
    )

    result = daemon._evaluate_cycle("cycle-expired", NOW, (expired_state,))

    assert result.paper_trades == 0
    assert result.abstain_records == 2
    assert paper.trades() == ()
    reasons = {(item.strategy, item.reason) for item in paper.abstains()}
    assert (StrategyKind.DIRECTIONAL_EDGE.value, "MARKET_WINDOW_EXPIRED") in reasons
    assert (StrategyKind.STRUCTURAL_ARBITRAGE.value, "MARKET_WINDOW_EXPIRED") in reasons


def test_router_snapshot_does_not_reserve_filled_positions_twice(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    paper.save_trade_snapshot(
        trade_id="filled-open",
        decision_id="decision:filled",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-filled",
        side="UP",
        status="OPEN",
        observed_at=NOW,
        payload={
            "stake": "10",
            "cost_basis_usdc": "10",
            "window_end": (NOW + timedelta(minutes=5)).isoformat(),
            "fill_status": "FILLED",
            "real_order_submission": False,
        },
    )
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    snapshot = daemon._router_snapshot_from_paper(NOW)
    portfolio = daemon._portfolio_state_from_paper(NOW)

    assert snapshot.claims == ()
    assert portfolio.bankroll_available == Decimal("30.00")
    assert tuple(item.capital_at_risk for item in portfolio.exposures) == (Decimal("10"),)


def test_paper_loss_cooldown_is_derived_from_ledger_settlement_time(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    for index in range(10):
        paper.save_trade_snapshot(
            trade_id=f"settled-loss-{index}",
            decision_id=f"decision:settled-loss-{index}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=f"condition-loss-{index}",
            side="UP",
            status="SETTLED",
            observed_at=NOW - timedelta(hours=2, minutes=index),
            payload={
                "stake": "1",
                "cost_basis_usdc": "1",
                "win_loss": "LOSS",
                "real_order_submission": False,
            },
        )
    paper.save_settlement_condition_attempt(
        condition_id="condition-loss-0",
        state="SETTLED",
        attempted_at=NOW - timedelta(hours=2),
        next_attempt_at=None,
        reason="SETTLED",
        successful_at=NOW - timedelta(hours=2),
    )
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    portfolio = daemon._portfolio_state_from_paper(NOW)

    assert portfolio.consecutive_losses == 10
    assert portfolio.cooldown_until == NOW - timedelta(hours=1)


def test_paper_loss_cooldown_can_be_active_from_recent_settlement(tmp_path) -> None:
    paper = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    shadow = SQLiteShadowRepository(tmp_path / "shadow.sqlite3")
    paper.initialize()
    shadow.initialize()
    for index in range(10):
        paper.save_trade_snapshot(
            trade_id=f"recent-loss-{index}",
            decision_id=f"decision:recent-loss-{index}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=f"condition-recent-loss-{index}",
            side="UP",
            status="SETTLED",
            observed_at=NOW - timedelta(minutes=30, seconds=index),
            payload={
                "stake": "1",
                "cost_basis_usdc": "1",
                "win_loss": "LOSS",
                "real_order_submission": False,
            },
        )
    paper.save_settlement_condition_attempt(
        condition_id="condition-recent-loss-0",
        state="SETTLED",
        attempted_at=NOW - timedelta(minutes=30),
        next_attempt_at=None,
        reason="SETTLED",
        successful_at=NOW - timedelta(minutes=30),
    )
    window = new_evidence_window(
        aws_user_id="user",
        aws_account="account",
        aws_arn="arn:aws:iam::123456789012:user/test",
        started_at=NOW,
        commit="abcdef1234567890",
    )
    daemon = ShadowDaemon(
        data_client=FixtureClient(),
        paper_repository=paper,
        shadow_repository=shadow,
        evidence_window=window,
        report_dir=tmp_path,
        clock=StaticClock(),
        poll_seconds=1,
    )

    portfolio = daemon._portfolio_state_from_paper(NOW)

    assert portfolio.consecutive_losses == 10
    assert portfolio.cooldown_until == NOW + timedelta(minutes=30)


def _market_state(bucket: MarketBucket) -> ShadowMarketState:
    market = Market(
        "market-btc-5m",
        "condition-btc-5m",
        bucket.asset,
        bucket.horizon,
        (
            MarketToken("token-up", OutcomeSide.UP),
            MarketToken("token-down", OutcomeSide.DOWN),
        ),
        NOW,
        NOW + timedelta(minutes=5),
        OFFICIAL_REFERENCE_SOURCES[bucket],
    )
    lineage = EventLineage(DataSource.POLYMARKET_CLOB, NOW, NOW, NOW, 1)
    discovery = MarketDiscovery(
        "event-btc-5m",
        "btc-updown-5m-1799877600",
        "BTC up or down",
        market,
        SettlementMetadata(
            market.market_id,
            market.condition_id,
            market.settlement_source,
            "rules",
            SettlementMethod.CHAINLINK_TWAP,
            "btc-5m-twap-60",
            bucket.asset,
            bucket.horizon,
            60,
            "1",
            lineage,
        ),
    )
    up = PolymarketBook(
        market.condition_id,
        "token-up",
        (PolymarketLevel(Decimal("0.39"), Decimal("10")),),
        (PolymarketLevel(Decimal("0.40"), Decimal("10")),),
        "hash-up",
        Decimal("2"),
        Decimal("0.01"),
        lineage,
    )
    down = PolymarketBook(
        market.condition_id,
        "token-down",
        (PolymarketLevel(Decimal("0.39"), Decimal("10")),),
        (PolymarketLevel(Decimal("0.40"), Decimal("10")),),
        "hash-down",
        Decimal("2"),
        Decimal("0.01"),
        lineage,
    )
    fee = FeeSchedule(
        market.condition_id,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("2"),
        lineage,
    )
    official = OfficialReference(
        "official-btc-5m",
        market.market_id,
        bucket.asset,
        Decimal("60000"),
        market.settlement_source,
        NOW,
        NOW,
        NOW,
        True,
    )
    proxy = ProxyReference(
        "proxy-btc-5m",
        market.market_id,
        bucket.asset,
        Decimal("60600"),
        "BINANCE_PROXY",
        NOW,
        NOW,
        NOW,
    )
    ptb = PriceToBeatRecord(
        market.condition_id,
        official,
        NOW,
        "ptb:btc:5m:condition-btc-5m",
    )
    features = build_directional_features(
        market,
        ptb,
        ExternalDirectionalSnapshot(
            proxy,
            Decimal("0.010"),
            Decimal("0.012"),
            Decimal("0.010"),
            Decimal("0.001"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0.20"),
            Decimal("0.010"),
            Decimal("0.20"),
            Decimal("0.90"),
            Decimal("0.05"),
            Decimal("0.20"),
            "BINANCE_EXTERNAL_FEATURES",
            NOW,
        ),
        generated_at=NOW,
        feature_set_version="v3.15.4-test-features",
    )
    return ShadowMarketState(
        bucket,
        discovery,
        up,
        down,
        fee,
        proxy,
        official,
        NOW,
        price_to_beat=ptb,
        directional_features=features,
        ptb_status="PTB_READY",
        ptb_reason="PTB_ESTABLISHED",
        feature_status="FEATURES_READY",
    )
