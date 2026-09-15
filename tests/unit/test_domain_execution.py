"""Domain tests for plans, order lifecycle, positions, settlement, and ledger."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    ExecutionPlan,
    Fill,
    LedgerEntry,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderStatus,
    OutcomeSide,
    Position,
    Settlement,
    StrategyKind,
    TimeInForce,
    TradingMode,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def make_intent(client_order_id: str = "client-order-1") -> OrderIntent:
    return OrderIntent(
        client_order_id=client_order_id,
        candidate_id="candidate-1",
        strategy=StrategyKind.DIRECTIONAL_EDGE,
        market_id="market-1",
        token_id="up-token",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("0.55"),
        time_in_force=TimeInForce.FOK,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
    )


def test_execution_plan_is_data_only_and_requires_unique_intents() -> None:
    intent = make_intent()
    plan = ExecutionPlan(
        plan_id="plan-1",
        decision_id="decision-1",
        risk_decision_id="risk-1",
        mode=TradingMode.PAPER,
        intents=(intent,),
        idempotency_key="cycle-1",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
    )

    assert plan.mode is TradingMode.PAPER
    for method_name in ("submit", "execute", "place_order", "cancel_order"):
        assert not hasattr(plan, method_name)

    with pytest.raises(ValueError, match="unique"):
        ExecutionPlan(
            plan_id="plan-2",
            decision_id="decision-1",
            risk_decision_id="risk-1",
            mode=TradingMode.PAPER,
            intents=(intent, intent),
            idempotency_key="cycle-2",
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=5),
        )


def test_order_acknowledgement_has_zero_fill() -> None:
    acknowledged = OrderState(
        client_order_id="client-order-1",
        exchange_order_id="exchange-order-1",
        status=OrderStatus.ACKNOWLEDGED,
        requested_quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        created_at=NOW,
        acknowledged_at=NOW + timedelta(milliseconds=10),
        updated_at=NOW + timedelta(milliseconds=10),
    )

    assert acknowledged.status is OrderStatus.ACKNOWLEDGED
    assert acknowledged.filled_quantity == Decimal("0")


def test_acknowledged_state_cannot_claim_a_fill() -> None:
    with pytest.raises(ValueError, match="cannot have filled"):
        OrderState(
            client_order_id="client-order-1",
            exchange_order_id="exchange-order-1",
            status=OrderStatus.ACKNOWLEDGED,
            requested_quantity=Decimal("10"),
            filled_quantity=Decimal("1"),
            average_fill_price=Decimal("0.50"),
            created_at=NOW,
            acknowledged_at=NOW + timedelta(milliseconds=10),
            updated_at=NOW + timedelta(milliseconds=10),
        )


@pytest.mark.parametrize(
    ("status", "filled_quantity", "average_fill_price"),
    [
        (OrderStatus.PARTIALLY_FILLED, Decimal("5"), Decimal("0.51")),
        (OrderStatus.FILLED, Decimal("10"), Decimal("0.52")),
        (OrderStatus.CANCELLED, Decimal("4"), Decimal("0.50")),
        (OrderStatus.UNKNOWN_ORDER_STATE, Decimal("2"), Decimal("0.49")),
    ],
)
def test_order_states_preserve_reconciled_fill_quantity(
    status: OrderStatus,
    filled_quantity: Decimal,
    average_fill_price: Decimal,
) -> None:
    state = OrderState(
        client_order_id="client-order-1",
        exchange_order_id="exchange-order-1",
        status=status,
        requested_quantity=Decimal("10"),
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        created_at=NOW,
        updated_at=NOW + timedelta(milliseconds=20),
    )

    assert state.filled_quantity == filled_quantity


@pytest.mark.parametrize(
    ("status", "filled_quantity"),
    [
        (OrderStatus.PARTIALLY_FILLED, Decimal("0")),
        (OrderStatus.PARTIALLY_FILLED, Decimal("10")),
        (OrderStatus.FILLED, Decimal("9")),
    ],
)
def test_order_states_reject_quantity_status_mismatch(
    status: OrderStatus,
    filled_quantity: Decimal,
) -> None:
    with pytest.raises(ValueError):
        OrderState(
            client_order_id="client-order-1",
            exchange_order_id="exchange-order-1",
            status=status,
            requested_quantity=Decimal("10"),
            filled_quantity=filled_quantity,
            average_fill_price=Decimal("0.50") if filled_quantity else None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_fill_position_settlement_and_ledger_reject_non_finite_money() -> None:
    with pytest.raises(ValueError, match="finite"):
        Fill(
            fill_id="fill-1",
            exchange_order_id="exchange-order-1",
            client_order_id="client-order-1",
            token_id="up-token",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("0.5"),
            fee=Decimal("NaN"),
            source_ts=NOW,
            recv_ts=NOW,
        )
    with pytest.raises(ValueError, match="finite"):
        Position(
            position_id="position-1",
            market_id="market-1",
            token_id="up-token",
            outcome=OutcomeSide.UP,
            quantity=Decimal("1"),
            average_price=Decimal("Infinity"),
            realized_pnl=Decimal("0"),
            opened_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="finite"):
        Settlement(
            settlement_id="settlement-1",
            market_id="market-1",
            winning_outcome=OutcomeSide.UP,
            payout_per_share=Decimal("NaN"),
            source="official-settlement",
            source_ts=NOW,
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="finite"):
        LedgerEntry(
            entry_id="entry-1",
            event_type="FILL",
            reference_id="fill-1",
            account="paper-cash",
            amount=Decimal("Infinity"),
            currency="USDC",
            occurred_at=NOW,
            recorded_at=NOW,
        )


def test_valid_fill_position_settlement_and_ledger_snapshots() -> None:
    fill = Fill(
        fill_id="fill-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        token_id="up-token",
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("0.50"),
        fee=Decimal("0.01"),
        source_ts=NOW,
        recv_ts=NOW + timedelta(milliseconds=10),
    )
    position = Position(
        position_id="position-1",
        market_id="market-1",
        token_id="up-token",
        outcome=OutcomeSide.UP,
        quantity=Decimal("2"),
        average_price=Decimal("0.50"),
        realized_pnl=Decimal("0"),
        opened_at=NOW,
        updated_at=NOW,
    )
    settlement = Settlement(
        settlement_id="settlement-1",
        market_id="market-1",
        winning_outcome=OutcomeSide.UP,
        payout_per_share=Decimal("1"),
        source="official-settlement",
        source_ts=NOW,
        recorded_at=NOW + timedelta(seconds=1),
    )
    ledger = LedgerEntry(
        entry_id="entry-1",
        event_type="FILL",
        reference_id=fill.fill_id,
        account="paper-position",
        amount=Decimal("1.01"),
        currency="USDC",
        occurred_at=NOW,
        recorded_at=NOW,
        metadata=(("mode", "PAPER"),),
    )

    assert position.quantity == fill.quantity
    assert settlement.payout_per_share == Decimal("1")
    assert ledger.metadata == (("mode", "PAPER"),)
