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

    async def market_metadata(self, condition_id):
        if isinstance(self.payload, dict) and condition_id in self.payload:
            return self.payload[condition_id]
        return self.payload

    async def resolve(self, *, condition_id, asset, horizon, expected_market_id=None):
        self.calls += 1
        payload = (
            self.payload[condition_id]
            if isinstance(self.payload, dict) and condition_id in self.payload
            else self.payload
        )
        return parse_gamma_official_settlement(
            payload,
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


def test_settlement_recovers_legacy_open_trade_identity_and_settles(tmp_path):
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

    assert resolver.calls == 1
    assert result["settlement_checked"] == 1
    assert result["identity_recovered"] == 1
    assert result["settlement_completed"] == 1
    assert trade is not None
    assert trade.status == "SETTLED"
    overlay = paper.identity_overlay("condition-legacy")
    assert overlay is not None
    assert overlay.status == "RECOVERED"
    assert overlay.market_id == "market-legacy"
    assert overlay.window_end == NOW


def test_blocked_legacy_condition_does_not_starve_later_condition(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    for condition in ("condition-bad", "condition-good"):
        paper.save_trade_snapshot(
            trade_id=f"trade-{condition}",
            decision_id=f"decision:{condition}",
            strategy="DIRECTIONAL_EDGE",
            asset="BTC",
            horizon="5m",
            condition_id=condition,
            side="UP",
            status="OPEN",
            observed_at=NOW - timedelta(minutes=10),
            payload={
                "market_id": f"market-{condition}",
                "condition_id": condition,
                "side": "UP",
                "stake": "1",
                "cost_basis_usdc": "1",
                "shares": "1",
                "real_order_submission": False,
            },
        )
    bad = _official("UP", condition_id="condition-bad", market_id="market-condition-bad")
    bad["cryptoMarketConfig"] = dict(bad["cryptoMarketConfig"]) | {"asset": "eth"}
    resolver = Resolver(
        {
            "condition-bad": bad,
            "condition-good": _official(
                "UP", condition_id="condition-good", market_id="market-condition-good"
            ),
        }
    )
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=resolver,
        clock=Clock(),
        max_trades_per_pass=5,
    )

    result = asyncio.run(service.run_once())

    assert result["settlement_checked"] == 2
    assert result["identity_blocked"] == 1
    assert result["identity_recovered"] == 1
    assert result["settlement_completed"] == 1
    assert paper.trade_by_id("trade-condition-good").status == "SETTLED"
    assert paper.trade_by_id("trade-condition-bad").status == "OPEN"


def test_condition_attempt_queue_prevents_starvation_across_restarts(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    for suffix in ("a", "b", "c", "d"):
        _trade(
            paper,
            trade_id=f"trade-{suffix}",
            condition_id=f"condition-{suffix}",
            market_id=f"market-{suffix}",
        )
    blocked = _official("UP", condition_id="condition-a", market_id="market-a")
    blocked["winningOutcome"] = ""
    pending = _official("UP", condition_id="condition-c", market_id="market-c") | {
        "closed": False
    }
    resolver = Resolver(
        {
            "condition-a": blocked,
            "condition-b": _official("UP", condition_id="condition-b", market_id="market-b"),
            "condition-c": pending,
            "condition-d": _official("UP", condition_id="condition-d", market_id="market-d"),
        }
    )

    first = asyncio.run(
        PaperSettlementService(
            paper_repository=paper,
            corpus_repository=corpus,
            resolver=resolver,
            clock=Clock(),
            max_conditions_per_pass=1,
        ).run_once()
    )
    second = asyncio.run(
        PaperSettlementService(
            paper_repository=paper,
            corpus_repository=corpus,
            resolver=resolver,
            clock=Clock(),
            max_conditions_per_pass=1,
        ).run_once()
    )
    third = asyncio.run(
        PaperSettlementService(
            paper_repository=paper,
            corpus_repository=corpus,
            resolver=resolver,
            clock=Clock(),
            max_conditions_per_pass=1,
        ).run_once()
    )
    fourth = asyncio.run(
        PaperSettlementService(
            paper_repository=paper,
            corpus_repository=corpus,
            resolver=resolver,
            clock=Clock(),
            max_conditions_per_pass=1,
        ).run_once()
    )

    assert first["conditions_attempted"] == ("condition-a",)
    assert first["settlement_blocked"] == 1
    assert second["conditions_attempted"] == ("condition-b",)
    assert second["settlement_completed"] == 1
    assert third["conditions_attempted"] == ("condition-c",)
    assert third["settlement_pending"] == 1
    assert fourth["conditions_attempted"] == ("condition-d",)
    assert fourth["settlement_completed"] == 1
    assert paper.trade_by_id("trade-b").status == "SETTLED"
    assert paper.trade_by_id("trade-d").status == "SETTLED"
    assert paper.trade_by_id("trade-a").status == "OPEN"
    assert paper.trade_by_id("trade-c").status == "OPEN"
    assert paper.settlement_attempt("condition-a").state == "BLOCKED_RETRYABLE"
    assert paper.settlement_attempt("condition-b").state == "SETTLED"
    assert paper.settlement_attempt("condition-c").state == "PENDING"
    assert paper.settlement_attempt("condition-d").state == "SETTLED"


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
        evidence_hash="hash-retrieved-again",
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


def test_official_settlement_requires_real_resolved_at_not_updated_at(tmp_path):
    result = parse_gamma_official_settlement(
        _official("UP") | {"resolvedAt": "", "closedTime": "", "updatedAt": NOW.isoformat()},
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )

    assert result.status is OfficialSettlementStatus.SETTLEMENT_BLOCKED
    assert result.reason == "OFFICIAL_RESOLVED_AT_MISSING"


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


def test_effective_identity_summary_uses_overlay_without_mutating_snapshot(tmp_path):
    paper = _paper(tmp_path)
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
    before = paper.trade_by_id("legacy-open")
    paper.save_identity_overlay_once(
        condition_id="condition-legacy",
        market_id="market-legacy",
        asset=Asset.BTC.value,
        horizon=Horizon.FIVE_MINUTES.value,
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW,
        outcome_tokens={"up": "up-token", "down": "down-token"},
        source_kind="OFFICIAL_METADATA",
        source="test",
        retrieved_at=NOW,
        verified_at=NOW,
        evidence_hash="identity-hash",
        status="RECOVERED",
        blocker_reason=None,
        payload={"source": "test"},
    )

    summary = paper.summary(now=NOW)
    after = paper.trade_by_id("legacy-open")

    assert summary["raw_snapshot_missing_window_end_count"] == 1
    assert summary["effective_identity_missing_trade_count"] == 0
    assert before is not None and after is not None
    assert before.payload == after.payload


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
        "eventStartTime": (NOW - timedelta(minutes=5)).isoformat(),
        "endDate": NOW.isoformat(),
        "resolutionSource": "Chainlink TWAP",
        "description": "BTC up or down test market",
        "version": "1",
        "cryptoMarketConfigId": "btc-5m",
        "cryptoMarketConfig": {
            "id": "btc-5m",
            "asset": "btc",
            "duration": "5m",
            "twapEnabled": True,
            "twapLookbackSeconds": 60,
        },
        "updatedAt": NOW.isoformat(),
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
