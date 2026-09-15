"""Deterministic PAPER gateway, reconciliation, cancellation, and settlement."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from direction_engine_v3.domain import (
    ExecutionPlan,
    Fill,
    OrderState,
    OrderStatus,
    OutcomeSide,
    Settlement,
    TradingMode,
)
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.storage.paper import SQLitePaperRepository, StoredExecution

_ZERO = Decimal("0")


class LiveGateway(Protocol):
    """Future guarded-LIVE boundary; V3.11 provides no implementation."""

    def execute(self, plan: ExecutionPlan) -> object: ...


@dataclass(frozen=True, slots=True)
class PaperFillEvidence:
    client_order_id: str
    filled_quantity: Decimal
    average_price: Decimal | None
    fee: Decimal
    source_ts: datetime
    recv_ts: datetime

    def __post_init__(self) -> None:
        require_text("client_order_id", self.client_order_id)
        require_decimal("filled_quantity", self.filled_quantity, minimum=_ZERO)
        require_decimal("fee", self.fee, minimum=_ZERO)
        require_utc("source_ts", self.source_ts)
        require_utc("recv_ts", self.recv_ts)
        if self.average_price is not None:
            require_decimal(
                "average_price", self.average_price, minimum=_ZERO, maximum=Decimal("1")
            )
        if (self.filled_quantity > _ZERO) != (self.average_price is not None):
            raise ValueError("positive fill and average price must be present together")


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    plan_id: str
    idempotency_key: str
    orders: tuple[OrderState, ...]
    fills: tuple[Fill, ...]
    recorded_at: datetime
    duplicate: bool = False


class PaperGateway:
    def __init__(self, repository: SQLitePaperRepository) -> None:
        self._repository = repository

    def execute(
        self,
        plan: ExecutionPlan,
        evidence: tuple[PaperFillEvidence, ...],
        *,
        now: datetime,
        kill_switch_active: bool,
        ledger_reconciled: bool,
    ) -> PaperExecutionResult:
        require_utc("now", now)
        if plan.mode is not TradingMode.PAPER:
            raise ValueError("PaperGateway accepts PAPER plans only")
        existing = self._repository.get(plan.idempotency_key)
        if existing is not None:
            return _restore(existing, duplicate=True)
        if kill_switch_active or not ledger_reconciled:
            raise RuntimeError("execution-time safety gate denied PAPER plan")
        if now > plan.expires_at:
            raise ValueError("execution plan expired")
        by_id = {item.client_order_id: item for item in evidence}
        if len(by_id) != len(evidence):
            raise ValueError("fill evidence client IDs must be unique")
        expected = {intent.client_order_id for intent in plan.intents}
        if set(by_id) != expected:
            raise ValueError("fill evidence must exactly match plan intents")
        orders = []
        fills = []
        for intent in plan.intents:
            item = by_id[intent.client_order_id]
            if item.filled_quantity > intent.quantity:
                raise ValueError("paper fill exceeds requested quantity")
            status = (
                OrderStatus.ACKNOWLEDGED
                if item.filled_quantity == _ZERO
                else OrderStatus.FILLED
                if item.filled_quantity == intent.quantity
                else OrderStatus.PARTIALLY_FILLED
            )
            exchange_order_id = f"paper:{intent.client_order_id}"
            orders.append(
                OrderState(
                    intent.client_order_id,
                    status,
                    intent.quantity,
                    item.filled_quantity,
                    now,
                    item.recv_ts,
                    exchange_order_id,
                    now,
                    item.average_price,
                )
            )
            if item.filled_quantity > _ZERO:
                assert item.average_price is not None
                fills.append(
                    Fill(
                        f"fill:{intent.client_order_id}",
                        exchange_order_id,
                        intent.client_order_id,
                        intent.token_id,
                        intent.side,
                        item.filled_quantity,
                        item.average_price,
                        item.fee,
                        item.source_ts,
                        item.recv_ts,
                    )
                )
        result = PaperExecutionResult(
            plan.plan_id, plan.idempotency_key, tuple(orders), tuple(fills), now
        )
        stored = self._repository.save_once(
            idempotency_key=plan.idempotency_key,
            plan_id=plan.plan_id,
            payload={"plan": asdict(plan), "result": asdict(result)},
            recorded_at=now,
        )
        return _restore(stored, duplicate=False)


def cancel_order(order: OrderState, *, cancelled_at: datetime) -> OrderState:
    require_utc("cancelled_at", cancelled_at)
    if order.status in {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }:
        raise ValueError("terminal order cannot be cancelled")
    return replace(order, status=OrderStatus.CANCELLED, updated_at=cancelled_at)


def reconcile_order(
    order: OrderState,
    fills: tuple[Fill, ...],
    *,
    reconciled_at: datetime,
    authoritative_final: bool,
) -> OrderState:
    require_utc("reconciled_at", reconciled_at)
    matching = tuple(item for item in fills if item.client_order_id == order.client_order_id)
    quantity = sum((item.quantity for item in matching), _ZERO)
    if quantity > order.requested_quantity:
        raise ValueError("reconciled fills exceed request")
    if not authoritative_final:
        return replace(order, status=OrderStatus.UNKNOWN_ORDER_STATE, updated_at=reconciled_at)
    if quantity == _ZERO:
        return replace(
            order,
            status=OrderStatus.ACKNOWLEDGED,
            filled_quantity=_ZERO,
            average_fill_price=None,
            updated_at=reconciled_at,
        )
    average = sum((item.quantity * item.price for item in matching), _ZERO) / quantity
    status = (
        OrderStatus.FILLED
        if quantity == order.requested_quantity
        else OrderStatus.PARTIALLY_FILLED
    )
    return replace(
        order,
        status=status,
        filled_quantity=quantity,
        average_fill_price=average,
        updated_at=reconciled_at,
    )


def settled_pnl(
    fills: tuple[Fill, ...], settlement: Settlement, *, outcome: OutcomeSide
) -> Decimal:
    """Realize PAPER PnL only from an authoritative settlement contract."""

    gross = sum(
        (
            item.quantity * settlement.payout_per_share
            if outcome is settlement.winning_outcome
            else _ZERO
            for item in fills
        ),
        _ZERO,
    )
    cost = sum((item.quantity * item.price + item.fee for item in fills), _ZERO)
    return gross - cost


def _restore(stored: StoredExecution, *, duplicate: bool) -> PaperExecutionResult:
    result = stored.payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("stored result is missing")
    orders = tuple(_restore_order(item) for item in result["orders"])
    fills = tuple(_restore_fill(item) for item in result["fills"])
    return PaperExecutionResult(
        str(result["plan_id"]),
        str(result["idempotency_key"]),
        orders,
        fills,
        datetime.fromisoformat(str(result["recorded_at"])),
        duplicate,
    )


def _restore_order(value: object) -> OrderState:
    if not isinstance(value, dict):
        raise RuntimeError("stored order is invalid")
    return OrderState(
        str(value["client_order_id"]),
        OrderStatus(str(value["status"])),
        Decimal(str(value["requested_quantity"])),
        Decimal(str(value["filled_quantity"])),
        datetime.fromisoformat(str(value["created_at"])),
        datetime.fromisoformat(str(value["updated_at"])),
        str(value["exchange_order_id"]) if value["exchange_order_id"] is not None else None,
        datetime.fromisoformat(str(value["acknowledged_at"]))
        if value["acknowledged_at"] is not None
        else None,
        Decimal(str(value["average_fill_price"]))
        if value["average_fill_price"] is not None
        else None,
    )


def _restore_fill(value: object) -> Fill:
    if not isinstance(value, dict):
        raise RuntimeError("stored fill is invalid")
    from direction_engine_v3.domain import OrderSide

    return Fill(
        str(value["fill_id"]),
        str(value["exchange_order_id"]),
        str(value["client_order_id"]),
        str(value["token_id"]),
        OrderSide(str(value["side"])),
        Decimal(str(value["quantity"])),
        Decimal(str(value["price"])),
        Decimal(str(value["fee"])),
        datetime.fromisoformat(str(value["source_ts"])),
        datetime.fromisoformat(str(value["recv_ts"])),
    )
