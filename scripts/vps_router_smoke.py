"""Deterministic V3.10 claim/conflict/reconciliation proof."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import OutcomeSide, StrategyCandidate, StrategyKind
from direction_engine_v3.router import (
    RouterPolicy,
    RouterSnapshot,
    RoutingOpportunity,
    reconcile_claim,
    route_opportunities,
)


def main() -> None:
    now = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
    candidate = StrategyCandidate(
        "candidate-1",
        StrategyKind.DIRECTIONAL_EDGE,
        "market-1",
        Decimal("5"),
        Decimal("1"),
        Decimal("0.8"),
        now,
        now + timedelta(seconds=5),
        OutcomeSide.UP,
    )
    opportunity = RoutingOpportunity(
        candidate, "condition-1", ("token-up",), Decimal("0.7"), Decimal("0.9")
    )
    policy = RouterPolicy(
        "router-v1",
        (
            StrategyKind.STRUCTURAL_ARBITRAGE,
            StrategyKind.DIRECTIONAL_EDGE,
            StrategyKind.DUAL40,
        ),
        False,
    )
    empty = RouterSnapshot(0, (), ())
    decision = route_opportunities(
        (opportunity,),
        snapshot=empty,
        available_capital=Decimal("10"),
        policy=policy,
        now=now + timedelta(milliseconds=1),
    )
    if decision.claim is None:
        raise RuntimeError("router did not propose claim")
    persisted = RouterSnapshot(1, (decision.claim,), ())
    duplicate = route_opportunities(
        (opportunity,),
        snapshot=persisted,
        available_capital=Decimal("10"),
        policy=policy,
        now=now + timedelta(milliseconds=2),
    )
    reconciled = reconcile_claim(
        persisted,
        claim_id=decision.claim.claim_id,
        reconciled_at=now + timedelta(milliseconds=3),
    )
    if duplicate.rejected != (("candidate-1", "DUPLICATE_CANDIDATE"),):
        raise RuntimeError("router did not preserve restart idempotency")
    print(
        json.dumps(
            {
                "claim": decision.claim.claim_id,
                "duplicate_reason": duplicate.rejected[0][1],
                "initial_revision": decision.expected_revision,
                "reconciled_revision": reconciled.revision,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
