import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import Asset, Horizon
from direction_engine_v3.settlement import (
    GammaOfficialSettlementResolver,
    OfficialSettlementStatus,
    PaperSettlementService,
    parse_gamma_official_settlement,
    parse_polymarket_official_settlement,
)
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
KNOWN_CONDITION_ID = "0xfb51a138e396ddb791dfbc95965af7ab80b4359e790cc19c6c057b4ff76f197e"
KNOWN_MARKET_ID = "4633348"


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


class DirectTransport:
    def __init__(self, gamma: dict[str, object], clob: dict[str, object]) -> None:
        self.gamma = gamma
        self.clob = clob
        self.urls: list[str] = []

    async def get_json(self, url, *, params=None):
        self.urls.append(url)
        if "/markets/" in url and "gamma-api.polymarket.com" in url:
            return self.gamma
        if "/markets/" in url and "clob.polymarket.com" in url:
            return self.clob
        if params and "condition_ids" in params:
            return []
        raise AssertionError(f"unexpected URL {url}")


def _gamma_direct_market(
    *,
    condition_id: str = "condition-1",
    market_id: str = "market-1",
    closed: bool = True,
    include_resolved_at: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": market_id,
        "conditionId": condition_id,
        "closed": closed,
        "active": not closed,
        "archived": False,
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["up-token", "down-token"]',
        "eventStartTime": (NOW - timedelta(minutes=15)).isoformat(),
        "endDate": (NOW - timedelta(minutes=1)).isoformat(),
        "resolutionSource": "Polymarket official market metadata",
        "description": "Sanitized crypto directional market",
        "updatedAt": NOW.isoformat(),
    }
    if include_resolved_at:
        payload["closedTime"] = NOW.isoformat()
    return payload


def _clob_direct_condition(
    *,
    condition_id: str = "condition-1",
    closed: bool = True,
    up_winner: bool = False,
    down_winner: bool = True,
) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "closed": closed,
        "tokens": [
            {"outcome": "Up", "token_id": "up-token", "winner": up_winner},
            {"outcome": "Down", "token_id": "down-token", "winner": down_winner},
        ],
    }


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


def test_direct_market_id_and_clob_condition_settle_known_condition_down(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    _trade(
        paper,
        trade_id="known-trade",
        condition_id=KNOWN_CONDITION_ID,
        market_id=KNOWN_MARKET_ID,
        side="UP",
        stake="2",
        shares="3",
    )
    transport = DirectTransport(
        _gamma_direct_market(condition_id=KNOWN_CONDITION_ID, market_id=KNOWN_MARKET_ID),
        _clob_direct_condition(condition_id=KNOWN_CONDITION_ID, up_winner=False, down_winner=True),
    )
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=GammaOfficialSettlementResolver(transport, Clock()),
        clock=Clock(),
    )

    result = asyncio.run(service.run_once())
    trade = paper.trade_by_id("known-trade")

    assert result["settlement_completed"] == 1
    assert trade is not None
    assert trade.status == "SETTLED"
    assert trade.payload["official_winning_side"] == "DOWN"
    assert trade.payload["win_loss"] == "LOSS"
    assert trade.payload["payout_usdc"] == "0"
    assert trade.payload["realized_paper_pnl"] == "-2"
    assert trade.payload["settlement_source_kind"] == "OFFICIAL"
    assert trade.payload["settlement_source"] == "POLYMARKET_GAMMA_MARKET_ID_AND_CLOB_CONDITION"
    assert trade.payload["real_order_submission"] is False
    assert any(url.endswith(f"/markets/{KNOWN_MARKET_ID}") for url in transport.urls)
    assert any(url.endswith(f"/markets/{KNOWN_CONDITION_ID}") for url in transport.urls)


def test_clob_up_winner_path_settles_up():
    result = parse_polymarket_official_settlement(
        _gamma_direct_market(),
        _clob_direct_condition(up_winner=True, down_winner=False),
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )

    assert result.status is OfficialSettlementStatus.SETTLED
    assert result.winning_side is not None
    assert result.winning_side.value == "UP"
    assert result.source == "POLYMARKET_GAMMA_MARKET_ID_AND_CLOB_CONDITION"


@pytest.mark.parametrize(
    ("gamma", "clob", "reason", "status"),
    (
        (
            _gamma_direct_market(),
            _clob_direct_condition(up_winner=False, down_winner=False),
            "CLOB_WINNER_CARDINALITY_INVALID",
            OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        ),
        (
            _gamma_direct_market(),
            _clob_direct_condition(up_winner=True, down_winner=True),
            "CLOB_WINNER_CARDINALITY_INVALID",
            OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        ),
        (
            _gamma_direct_market(),
            {
                "condition_id": "condition-1",
                "closed": True,
                "tokens": [
                    {"outcome": "Moon", "winner": True},
                    {"outcome": "Down", "winner": False},
                ],
            },
            "CLOB_TOKEN_OUTCOME_UNKNOWN",
            OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        ),
        (
            _gamma_direct_market(),
            _clob_direct_condition(condition_id="condition-other"),
            "CLOB_CONDITION_ID_MISMATCH",
            OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        ),
        (
            _gamma_direct_market(market_id="market-other"),
            _clob_direct_condition(),
            "GAMMA_MARKET_ID_MISMATCH",
            OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        ),
        (
            _gamma_direct_market(closed=False),
            _clob_direct_condition(),
            "MARKET_NOT_FINAL",
            OfficialSettlementStatus.SETTLEMENT_PENDING,
        ),
    ),
)
def test_direct_gamma_clob_settlement_fail_closed(
    gamma: dict[str, object],
    clob: dict[str, object],
    reason: str,
    status: OfficialSettlementStatus,
):
    result = parse_polymarket_official_settlement(
        gamma,
        clob,
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )

    assert result.status is status
    assert result.reason == reason


def test_clob_prices_are_never_used_to_infer_winner():
    clob = _clob_direct_condition(up_winner=False, down_winner=False)
    clob["tokens"] = [
        {"outcome": "Up", "winner": False, "price": "0.99"},
        {"outcome": "Down", "winner": False, "price": "0.01"},
    ]

    result = parse_polymarket_official_settlement(
        _gamma_direct_market(),
        clob,
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        condition_id="condition-1",
        expected_market_id="market-1",
        observed_at=NOW,
    )

    assert result.status is OfficialSettlementStatus.SETTLEMENT_BLOCKED
    assert result.reason == "CLOB_WINNER_CARDINALITY_INVALID"
    assert result.winning_side is None


def test_settlement_persists_when_corpus_label_blocked_by_missing_resolution_time(tmp_path):
    paper = _paper(tmp_path)
    corpus = _corpus(tmp_path)
    _trade(paper, condition_id="condition-1", market_id="market-1", side="DOWN")
    transport = DirectTransport(
        _gamma_direct_market(include_resolved_at=False),
        _clob_direct_condition(up_winner=False, down_winner=True),
    )
    service = PaperSettlementService(
        paper_repository=paper,
        corpus_repository=corpus,
        resolver=GammaOfficialSettlementResolver(transport, Clock()),
        clock=Clock(),
    )

    result = asyncio.run(service.run_once())
    trade = paper.trade_by_id("trade-1")
    settlement = paper.settlement_for_trade("trade-1")

    assert result["settlement_completed"] == 1
    assert trade is not None and trade.status == "SETTLED"
    assert settlement is not None
    assert settlement.official_resolved_at is None
    assert settlement.official_resolution_observed_at == NOW
    task = paper.corpus_label_task("condition-1", settlement.evidence_hash)
    assert task is not None
    assert task.state == "BLOCKED_RETRYABLE"
    assert task.last_reason == "CORPUS_LABEL_BLOCKED:RESOLUTION_TIME_UNAVAILABLE"


def test_old_retryable_market_metadata_not_found_is_due_under_v2(tmp_path):
    paper = _paper(tmp_path)
    paper.save_settlement_condition_attempt(
        condition_id="condition-legacy",
        state="BLOCKED_RETRYABLE",
        attempted_at=NOW,
        next_attempt_at=NOW + timedelta(hours=6),
        reason="MARKET_METADATA_NOT_FOUND",
        error=None,
        successful_at=None,
        payload={
            "condition_id": "condition-legacy",
            "resolver_version": "POLYMARKET_SETTLEMENT_V1",
            "reason": "MARKET_METADATA_NOT_FOUND",
        },
    )

    due = paper.due_settlement_conditions(("condition-legacy",), now=NOW)

    assert due == ("condition-legacy",)


def test_paper_settlement_schema_migration_keeps_old_rows_and_adds_observed_at(tmp_path):
    path = tmp_path / "paper.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE paper_trade_settlements (
                settlement_id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL UNIQUE,
                condition_id TEXT NOT NULL,
                official_winning_side TEXT NOT NULL,
                selected_side TEXT NOT NULL,
                settlement_source_kind TEXT NOT NULL,
                settlement_source TEXT NOT NULL,
                official_resolved_at TEXT NOT NULL,
                settled_at TEXT NOT NULL,
                filled_shares TEXT NOT NULL,
                cost_basis_usdc TEXT NOT NULL,
                payout_usdc TEXT NOT NULL,
                realized_paper_pnl TEXT NOT NULL,
                win_loss TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO paper_trade_settlements
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "settlement:old",
                "trade-old",
                "condition-old",
                "DOWN",
                "UP",
                "OFFICIAL",
                "POLYMARKET_OFFICIAL_METADATA",
                NOW.isoformat(),
                NOW.isoformat(),
                "1",
                "0.5",
                "0",
                "-0.5",
                "LOSS",
                "old-hash",
                '{"settlement_source_kind":"OFFICIAL"}',
            ),
        )

    repo = SQLitePaperRepository(path)
    repo.initialize()
    settlement = repo.settlement_for_trade("trade-old")

    assert settlement is not None
    assert settlement.official_resolved_at == NOW
    assert settlement.official_resolution_observed_at == NOW
    with sqlite3.connect(path) as connection:
        columns = connection.execute("PRAGMA table_info(paper_trade_settlements)").fetchall()
    by_name = {str(row[1]): row for row in columns}
    assert "official_resolution_observed_at" in by_name
    assert int(by_name["official_resolved_at"][3]) == 0


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

    assert summary["initial_equity"] == "40.00"
    assert summary["raw_available_capital"] == "-1160.00"
    assert summary["available_capital"] == "-1160.00"
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
        official_resolution_observed_at=NOW,
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
        official_resolution_observed_at=NOW,
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
        official_resolution_observed_at=NOW,
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
            official_resolution_observed_at=NOW,
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
            "window_start": (NOW - timedelta(minutes=6)).isoformat(),
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
