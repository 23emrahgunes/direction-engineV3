"""Deterministic V3.9 proof of healthy approval and kill-switch denial."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    OutcomeSide,
    StrategyCandidate,
    StrategyKind,
)
from direction_engine_v3.risk import LiquidityEvidence, PortfolioState, RiskPolicy, assess_candidate


def main() -> None:
    now = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    candidate = StrategyCandidate(
        "smoke-candidate",
        StrategyKind.DIRECTIONAL_EDGE,
        "smoke-market",
        Decimal("5"),
        Decimal("0.5"),
        Decimal("0.7"),
        now,
        now + timedelta(seconds=5),
        OutcomeSide.UP,
    )
    liquidity = LiquidityEvidence(
        now,
        timedelta(milliseconds=50),
        Decimal("0.01"),
        Decimal("10"),
        Decimal("5"),
        Decimal("0.01"),
        Decimal("0.9"),
        5,
        True,
        True,
        True,
    )
    policy = RiskPolicy(
        3,
        Decimal("30"),
        Decimal("20"),
        Decimal("20"),
        Decimal("25"),
        2,
        Decimal("5"),
        Decimal("10"),
        Decimal("10"),
        3,
        timedelta(seconds=1),
        timedelta(seconds=1),
        Decimal("0.05"),
        Decimal("0.03"),
        Decimal("0.7"),
        3,
    )
    state = PortfolioState(
        Decimal("100"), (), Decimal("0"), Decimal("0"), 0, None, False, True, now
    )
    assessed_at = now + timedelta(milliseconds=100)
    approved = assess_candidate(
        candidate,
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        liquidity=liquidity,
        state=state,
        policy=policy,
        assessed_at=assessed_at,
        risk_decision_id="risk-approved",
    )
    killed = assess_candidate(
        candidate,
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        liquidity=liquidity,
        state=PortfolioState(
            Decimal("100"), (), Decimal("0"), Decimal("0"), 0, None, True, True, now
        ),
        policy=policy,
        assessed_at=assessed_at,
        risk_decision_id="risk-killed",
    )
    if not approved.approved or killed.reason_codes != ("GLOBAL_KILL_SWITCH",):
        raise RuntimeError("risk smoke did not preserve fail-closed behavior")
    print(
        json.dumps(
            {
                "approved_capital": str(approved.approved_capital),
                "kill_switch_approved": killed.approved,
                "kill_switch_reasons": killed.reason_codes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
