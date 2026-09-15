from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    ExecutionPlan,
    Fill,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OutcomeSide,
    Settlement,
    StrategyKind,
    TimeInForce,
    TradingMode,
)
from direction_engine_v3.execution import (
    PaperFillEvidence,
    PaperGateway,
    cancel_order,
    reconcile_order,
    settled_pnl,
)
from direction_engine_v3.storage import SQLitePaperRepository

NOW = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)


def plan(*, mode: TradingMode = TradingMode.PAPER) -> ExecutionPlan:
    intent = OrderIntent(
        "client-1",
        "candidate-1",
        StrategyKind.DIRECTIONAL_EDGE,
        "market-1",
        "token-up",
        OrderSide.BUY,
        Decimal("10"),
        Decimal("0.6"),
        TimeInForce.FAK,
        NOW,
        NOW + timedelta(seconds=5),
    )
    return ExecutionPlan(
        "plan-1",
        "decision-1",
        "risk-1",
        mode,
        (intent,),
        "idem-1",
        NOW,
        NOW + timedelta(seconds=5),
    )


def evidence(*, quantity: str, price: str | None, fee: str = "0") -> PaperFillEvidence:
    return PaperFillEvidence(
        "client-1",
        Decimal(quantity),
        Decimal(price) if price is not None else None,
        Decimal(fee),
        NOW + timedelta(milliseconds=1),
        NOW + timedelta(milliseconds=2),
    )


def gateway(tmp_path) -> tuple[PaperGateway, SQLitePaperRepository]:
    repository = SQLitePaperRepository(tmp_path / "paper.sqlite3")
    repository.initialize()
    return PaperGateway(repository), repository


@pytest.mark.parametrize(
    ("quantity", "price", "status"),
    [
        ("0", None, OrderStatus.ACKNOWLEDGED),
        ("4", "0.5", OrderStatus.PARTIALLY_FILLED),
        ("10", "0.5", OrderStatus.FILLED),
    ],
)
def test_paper_gateway_distinguishes_ack_partial_and_fill(
    tmp_path, quantity: str, price: str | None, status: OrderStatus
) -> None:
    paper, _ = gateway(tmp_path)
    result = paper.execute(
        plan(),
        (evidence(quantity=quantity, price=price),),
        now=NOW + timedelta(milliseconds=1),
        kill_switch_active=False,
        ledger_reconciled=True,
    )
    assert result.orders[0].status is status
    assert len(result.fills) == (0 if quantity == "0" else 1)


def test_restart_retry_is_idempotent_and_audit_is_append_once(tmp_path) -> None:
    paper, repository = gateway(tmp_path)
    first = paper.execute(
        plan(),
        (evidence(quantity="4", price="0.5", fee="0.01"),),
        now=NOW + timedelta(milliseconds=1),
        kill_switch_active=False,
        ledger_reconciled=True,
    )
    restarted = PaperGateway(repository)
    duplicate = restarted.execute(
        plan(),
        (evidence(quantity="10", price="0.4"),),
        now=NOW + timedelta(milliseconds=3),
        kill_switch_active=False,
        ledger_reconciled=True,
    )
    assert not first.duplicate
    assert duplicate.duplicate
    assert duplicate.orders == first.orders
    assert repository.audit_count("plan-1") == 1


def test_gateway_rejects_live_mode_and_execution_time_safety_failure(tmp_path) -> None:
    paper, _ = gateway(tmp_path)
    with pytest.raises(ValueError, match="PAPER"):
        paper.execute(
            plan(mode=TradingMode.LIVE),
            (evidence(quantity="0", price=None),),
            now=NOW,
            kill_switch_active=False,
            ledger_reconciled=True,
        )
    with pytest.raises(RuntimeError, match="safety"):
        paper.execute(
            plan(),
            (evidence(quantity="0", price=None),),
            now=NOW,
            kill_switch_active=True,
            ledger_reconciled=True,
        )


def test_cancellation_preserves_partial_fill_and_ack_is_not_fill(tmp_path) -> None:
    paper, _ = gateway(tmp_path)
    result = paper.execute(
        plan(),
        (evidence(quantity="4", price="0.5"),),
        now=NOW + timedelta(milliseconds=1),
        kill_switch_active=False,
        ledger_reconciled=True,
    )
    cancelled = cancel_order(result.orders[0], cancelled_at=NOW + timedelta(seconds=1))
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == Decimal("4")


def test_uncertain_reconciliation_is_unknown_and_authoritative_fills_win(tmp_path) -> None:
    paper, _ = gateway(tmp_path)
    result = paper.execute(
        plan(),
        (evidence(quantity="0", price=None),),
        now=NOW + timedelta(milliseconds=1),
        kill_switch_active=False,
        ledger_reconciled=True,
    )
    unknown = reconcile_order(
        result.orders[0], (), reconciled_at=NOW + timedelta(seconds=1), authoritative_final=False
    )
    assert unknown.status is OrderStatus.UNKNOWN_ORDER_STATE
    fill = Fill(
        "fill-final",
        "paper:client-1",
        "client-1",
        "token-up",
        OrderSide.BUY,
        Decimal("10"),
        Decimal("0.5"),
        Decimal("0.1"),
        NOW,
        NOW + timedelta(seconds=1),
    )
    reconciled = reconcile_order(
        unknown,
        (fill,),
        reconciled_at=NOW + timedelta(seconds=2),
        authoritative_final=True,
    )
    assert reconciled.status is OrderStatus.FILLED


def test_settlement_realizes_pnl_only_from_official_result() -> None:
    fill = Fill(
        "fill-1",
        "paper:client-1",
        "client-1",
        "token-up",
        OrderSide.BUY,
        Decimal("10"),
        Decimal("0.4"),
        Decimal("0.1"),
        NOW,
        NOW,
    )
    settlement = Settlement(
        "settlement-1",
        "market-1",
        OutcomeSide.UP,
        Decimal("1"),
        "official",
        NOW,
        NOW,
    )
    assert settled_pnl((fill,), settlement, outcome=OutcomeSide.UP) == Decimal("5.9")
