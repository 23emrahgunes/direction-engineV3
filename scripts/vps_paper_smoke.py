"""Deterministic V3.11 durable PAPER idempotency proof."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from direction_engine_v3.domain import (
    ExecutionPlan,
    OrderIntent,
    OrderSide,
    StrategyKind,
    TimeInForce,
    TradingMode,
)
from direction_engine_v3.execution import PaperFillEvidence, PaperGateway
from direction_engine_v3.storage import SQLitePaperRepository


def main() -> None:
    now = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
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
        now,
        now + timedelta(seconds=5),
    )
    plan = ExecutionPlan(
        "plan-1",
        "decision-1",
        "risk-1",
        TradingMode.PAPER,
        (intent,),
        "idempotency-1",
        now,
        now + timedelta(seconds=5),
    )
    evidence = PaperFillEvidence(
        "client-1",
        Decimal("4"),
        Decimal("0.5"),
        Decimal("0.01"),
        now,
        now + timedelta(milliseconds=1),
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        repository = SQLitePaperRepository(Path(directory) / "paper.sqlite3")
        repository.initialize()
        first = PaperGateway(repository).execute(
            plan,
            (evidence,),
            now=now + timedelta(milliseconds=1),
            kill_switch_active=False,
            ledger_reconciled=True,
        )
        duplicate = PaperGateway(repository).execute(
            plan,
            (evidence,),
            now=now + timedelta(milliseconds=2),
            kill_switch_active=False,
            ledger_reconciled=True,
        )
        if first.duplicate or not duplicate.duplicate or repository.audit_count("plan-1") != 1:
            raise RuntimeError("durable PAPER idempotency failed")
        print(
            json.dumps(
                {
                    "audit_events": 1,
                    "filled_quantity": str(first.orders[0].filled_quantity),
                    "first_status": first.orders[0].status.value,
                    "restart_duplicate": duplicate.duplicate,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
