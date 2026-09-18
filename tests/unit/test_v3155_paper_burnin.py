import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.settlement import (
    OfficialSettlementStatus,
    PaperSettlementService,
    parse_gamma_official_settlement,
)
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class Clock:
    def utc_now(self) -> datetime:
        return NOW


class Resolver:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def resolve(self, *, condition_id, asset, horizon, expected_market_id=None):
        self.calls += 1
        return parse_gamma_official_settlement(
            self.payload,
            asset=asset,
            horizon=horizon,
            condition_id=condition_id,
            expected_market_id=expected_market_id,
            observed_at=NOW,
        )


def test_official_up_settlement_settles_up_trade_as_win_and_no_fee_double_count(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    _trade(paper, side="UP", stake="3.04150", shares="5", fee="0.25")
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=Resolver(_official("UP")),
        clock=Clock(),
    )

    result = asyncio.run(service.run_once())
    trade = paper.trade_by_id("trade-1")

    assert result["settlement_completed"] == 1
    assert trade is not None
    assert trade.status == "SETTLED"
    assert trade.payload["win_loss"] == "WIN"
    assert trade.payload["payout_usdc"] == "5"
    assert trade.payload["realized_paper_pnl"] == "1.95850"


def test_official_down_settlement_settles_up_trade_as_loss(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    _trade(paper, side="UP", stake="3.04150", shares="5")
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=Resolver(_official("DOWN")),
        clock=Clock(),
    )

    asyncio.run(service.run_once())
    trade = paper.trade_by_id("trade-1")

    assert trade is not None
    assert trade.payload["win_loss"] == "LOSS"
    assert trade.payload["payout_usdc"] == "0"
    assert trade.payload["realized_paper_pnl"] == "-3.04150"


def test_paper_summary_preserves_negative_raw_capital_and_accounting_buckets(tmp_path):
    paper = _paper(tmp_path)
    _trade(
        paper,
        trade_id="open-over-budget",
        condition_id="condition-open",
        stake="1200",
        shares="1200",
        fee="0",
    )

    summary = paper.summary(now=NOW)

    assert summary["initial_equity"] == "1000"
    assert summary["raw_available_capital"] == "-200"
    assert summary["available_capital"] == "-200"
    assert summary["spendable_capital"] == "0"
    assert summary["open_cost_basis"] == "1200"
    assert summary["expired_but_unsettled_cost_basis"] == "1200"
    assert summary["unfilled_reservations"] == "0"
    assert summary["open_trade_count"] == 1
    assert summary["open_unique_condition_count"] == 1


def test_settlement_scan_reports_legacy_open_trade_missing_window_end(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    paper.save_trade_snapshot(
        trade_id="legacy-open",
        decision_id="decision:legacy",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id="condition-legacy",
        side="UP",
        status="OPEN",
        observed_at=NOW - timedelta(minutes=10),
        payload={
            "market_id": "market-legacy",
            "condition_id": "condition-legacy",
            "side": "UP",
            "stake": "1",
            "cost_basis_usdc": "1",
            "shares": "1",
            "real_order_submission": False,
        },
    )
    resolver = Resolver(_official("UP", condition_id="condition-legacy", market_id="market-legacy"))
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=resolver,
        clock=Clock(),
    )

    result = asyncio.run(service.run_once())
    trade = paper.trade_by_id("legacy-open")

    assert resolver.calls == 0
    assert result["settlement_checked"] == 1
    assert result["settlement_blocked"] == 1
    assert (
        result["last_settlement_error"]
        == "condition-legacy:SETTLEMENT_BLOCKED:LEGACY_MARKET_IDENTITY_INCOMPLETE"
    )
    assert trade is not None
    assert trade.status == "OPEN"


def test_pending_ambiguous_void_and_conflict_fail_closed(tmp_path):
    pending = parse_gamma_official_settlement(
        _official("UP") | {"closed": False},
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )
    ambiguous = parse_gamma_official_settlement(
        _official("UP") | {"winningOutcome": ""},
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )
    void = parse_gamma_official_settlement(
        _official("UP") | {"voided": True},
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )

    assert pending.status is OfficialSettlementStatus.SETTLEMENT_PENDING
    assert ambiguous.status is OfficialSettlementStatus.SETTLEMENT_BLOCKED
    assert void.status is OfficialSettlementStatus.VOID

    paper = _paper(tmp_path)
    _trade(paper)
    settlement = paper.save_settlement_once(
        settlement_id="s1",
        trade_id="trade-1",
        condition_id="condition-1",
        official_winning_side="UP",
        selected_side="UP",
        settlement_source_kind="OFFICIAL",
        settlement_source="POLYMARKET_OFFICIAL_METADATA",
        official_resolved_at=NOW,
        settled_at=NOW,
        filled_shares=Decimal("1"),
        cost_basis_usdc=Decimal("0.5"),
        payout_usdc=Decimal("1"),
        realized_paper_pnl=Decimal("0.5"),
        win_loss="WIN",
        evidence_hash="hash-1",
        payload={"settlement_source_kind": "OFFICIAL"},
    )
    assert paper.save_settlement_once(
        settlement_id="s1",
        trade_id="trade-1",
        condition_id="condition-1",
        official_winning_side="UP",
        selected_side="UP",
        settlement_source_kind="OFFICIAL",
        settlement_source="POLYMARKET_OFFICIAL_METADATA",
        official_resolved_at=NOW,
        settled_at=NOW,
        filled_shares=Decimal("1"),
        cost_basis_usdc=Decimal("0.5"),
        payout_usdc=Decimal("1"),
        realized_paper_pnl=Decimal("0.5"),
        win_loss="WIN",
        evidence_hash="hash-1",
        payload={"settlement_source_kind": "OFFICIAL"},
    ) == settlement
    with pytest.raises(RuntimeError, match="conflicting"):
        paper.save_settlement_once(
            settlement_id="s2",
            trade_id="trade-1",
            condition_id="condition-1",
            official_winning_side="DOWN",
            selected_side="UP",
            settlement_source_kind="OFFICIAL",
            settlement_source="POLYMARKET_OFFICIAL_METADATA",
            official_resolved_at=NOW,
            settled_at=NOW,
            filled_shares=Decimal("1"),
            cost_basis_usdc=Decimal("0.5"),
            payout_usdc=Decimal("0"),
            realized_paper_pnl=Decimal("-0.5"),
            win_loss="LOSS",
            evidence_hash="hash-2",
            payload={"settlement_source_kind": "OFFICIAL"},
        )


def test_corpus_condition_labeling_rejects_proxy_conflict_and_post_outcome(tmp_path):
    corpus = _corpus(tmp_path)
    corpus.save_pre_outcome(
        record_id="pre-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        observed_at=NOW - timedelta(minutes=1),
        payload=_training_payload(),
    )
    corpus.save_pre_outcome(
        record_id="post-1",
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        observed_at=NOW + timedelta(seconds=1),
        payload=_training_payload(),
    )

    with pytest.raises(ValueError, match="official"):
        corpus.attach_verified_outcome_to_condition_once(
            condition_id="condition-1",
            outcome={"settlement_source_kind": "PROXY", "outcome_up": True},
            official_resolved_at=NOW,
            attached_at=NOW,
        )
    attached = corpus.attach_verified_outcome_to_condition_once(
        condition_id="condition-1",
        outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": True},
        official_resolved_at=NOW,
        attached_at=NOW,
    )

    assert attached == 1
    assert corpus.get("pre-1").outcome_attached is True
    assert corpus.get("post-1").outcome_attached is False
    assert corpus.corpus_counts(asset=Asset.BTC, horizon=Horizon.FIVE_MINUTES) == {
        "observation_count": 2,
        "labeled_observation_count": 1,
        "unique_condition_count": 1,
        "labeled_unique_condition_count": 1,
    }
    with pytest.raises(RuntimeError, match="conflicting"):
        corpus.attach_verified_outcome_to_condition_once(
            condition_id="condition-1",
            outcome={"settlement_source_kind": "OFFICIAL", "outcome_up": False},
            official_resolved_at=NOW,
            attached_at=NOW,
        )


def test_settlement_pass_is_bounded_per_cycle(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    for index in range(3):
        _trade(
            paper,
            trade_id=f"trade-{index}",
            condition_id=f"condition-{index}",
            market_id=f"market-{index}",
        )
    resolver = Resolver(_official("UP", condition_id="condition-0", market_id="market-0"))
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=resolver,
        clock=Clock(),
        max_trades_per_pass=1,
    )

    result = asyncio.run(service.run_once())

    assert resolver.calls == 1
    assert result["settlement_checked"] == 1


def _paper(tmp_path) -> SQLitePaperRepository:
    repo = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repo.initialize()
    return repo


def _corpus(tmp_path) -> SQLiteDirectionalCorpusRepository:
    repo = SQLiteDirectionalCorpusRepository(tmp_path / "corpus.sqlite3")
    repo.initialize()
    return repo


def _trade(
    paper: SQLitePaperRepository,
    *,
    trade_id: str = "trade-1",
    condition_id: str = "condition-1",
    market_id: str = "market-1",
    side: str = "UP",
    stake: str = "0.5",
    shares: str = "1",
    fee: str = "0",
) -> None:
    paper.save_trade_snapshot(
        trade_id=trade_id,
        decision_id=f"decision:{trade_id}",
        strategy="DIRECTIONAL_EDGE",
        asset="BTC",
        horizon="5m",
        condition_id=condition_id,
        side=side,
        status="OPEN",
        observed_at=NOW - timedelta(minutes=10),
        payload={
            "market_id": market_id,
            "condition_id": condition_id,
            "side": side,
            "stake": stake,
            "cost_basis_usdc": stake,
            "shares": shares,
            "fee": fee,
            "window_end": (NOW - timedelta(minutes=1)).isoformat(),
            "real_order_submission": False,
        },
    )


def _official(
    winner: str, *, condition_id: str = "condition-1", market_id: str = "market-1"
) -> dict[str, object]:
    return {
        "id": market_id,
        "conditionId": condition_id,
        "closed": True,
        "active": False,
        "outcomes": '["Down", "Up"]',
        "clobTokenIds": '["down-token", "up-token"]',
        "winningOutcome": winner,
        "resolvedAt": NOW.isoformat(),
    }


def _training_payload() -> dict[str, object]:
    return {
        "feature_vector": {
            "feature_set_version": "test",
            "features": [{"name": "ptb_distance", "value": "0.1", "source_ts": NOW.isoformat()}],
        },
        "price_to_beat": {"persistence_id": "ptb-1", "value": "1"},
    }
