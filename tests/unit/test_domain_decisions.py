"""Domain tests for strategy and risk decisions."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from direction_engine_v3.domain import (
    DecisionAction,
    OutcomeSide,
    RiskDecision,
    StrategyCandidate,
    StrategyDecision,
    StrategyKind,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def make_directional_candidate() -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id="candidate-1",
        strategy=StrategyKind.DIRECTIONAL_EDGE,
        market_id="market-1",
        required_capital=Decimal("10"),
        expected_net_value=Decimal("0.50"),
        quality_score=Decimal("0.80"),
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
        outcome_side=OutcomeSide.UP,
    )


def test_directional_candidate_is_immutable_and_requires_side() -> None:
    candidate = make_directional_candidate()

    with pytest.raises(FrozenInstanceError):
        candidate.required_capital = Decimal("20")  # type: ignore[misc]

    with pytest.raises(ValueError, match="outcome_side"):
        StrategyCandidate(
            candidate_id="candidate-2",
            strategy=StrategyKind.DIRECTIONAL_EDGE,
            market_id="market-1",
            required_capital=Decimal("10"),
            expected_net_value=Decimal("0.50"),
            quality_score=Decimal("0.80"),
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=5),
        )


def test_structural_candidate_cannot_carry_directional_probability_side() -> None:
    with pytest.raises(ValueError, match="cannot carry"):
        StrategyCandidate(
            candidate_id="candidate-1",
            strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
            market_id="market-1",
            required_capital=Decimal("10"),
            expected_net_value=Decimal("0.50"),
            quality_score=Decimal("0.80"),
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=5),
            outcome_side=OutcomeSide.UP,
        )


def test_abstain_is_a_first_class_decision_without_candidate() -> None:
    decision = StrategyDecision(
        decision_id="decision-1",
        strategy=StrategyKind.DIRECTIONAL_EDGE,
        market_id="market-1",
        action=DecisionAction.ABSTAIN,
        decided_at=NOW,
        reason="official_reference_stale",
    )

    assert decision.action is DecisionAction.ABSTAIN
    assert decision.candidate is None


def test_trade_requires_matching_unexpired_candidate() -> None:
    candidate = make_directional_candidate()
    decision = StrategyDecision(
        decision_id="decision-1",
        strategy=StrategyKind.DIRECTIONAL_EDGE,
        market_id="market-1",
        action=DecisionAction.TRADE,
        decided_at=NOW + timedelta(seconds=1),
        reason="candidate_passed_strategy_gates",
        candidate=candidate,
    )

    assert decision.candidate is candidate

    with pytest.raises(ValueError, match="require a candidate"):
        StrategyDecision(
            decision_id="decision-2",
            strategy=StrategyKind.DIRECTIONAL_EDGE,
            market_id="market-1",
            action=DecisionAction.TRADE,
            decided_at=NOW,
            reason="invalid",
        )
    with pytest.raises(ValueError, match="expired"):
        StrategyDecision(
            decision_id="decision-3",
            strategy=StrategyKind.DIRECTIONAL_EDGE,
            market_id="market-1",
            action=DecisionAction.TRADE,
            decided_at=candidate.expires_at + timedelta(microseconds=1),
            reason="invalid",
            candidate=candidate,
        )


def test_denied_risk_decision_fails_closed_with_reason() -> None:
    denied = RiskDecision(
        risk_decision_id="risk-1",
        candidate_id="candidate-1",
        approved=False,
        approved_capital=Decimal("0"),
        assessed_at=NOW,
        reason_codes=("REFERENCE_STALE",),
    )

    assert denied.approved is False

    with pytest.raises(ValueError, match="reason"):
        RiskDecision(
            risk_decision_id="risk-2",
            candidate_id="candidate-1",
            approved=False,
            approved_capital=Decimal("0"),
            assessed_at=NOW,
            reason_codes=(),
        )
    with pytest.raises(ValueError, match="zero capital"):
        RiskDecision(
            risk_decision_id="risk-3",
            candidate_id="candidate-1",
            approved=False,
            approved_capital=Decimal("1"),
            assessed_at=NOW,
            reason_codes=("REFERENCE_STALE",),
        )


def test_approved_risk_decision_requires_positive_capital() -> None:
    with pytest.raises(ValueError, match="positive"):
        RiskDecision(
            risk_decision_id="risk-1",
            candidate_id="candidate-1",
            approved=True,
            approved_capital=Decimal("0"),
            assessed_at=NOW,
            reason_codes=(),
        )


@pytest.mark.parametrize("method_name", ["submit", "execute", "place_order", "cancel_order"])
def test_strategy_snapshots_have_no_execution_methods(method_name: str) -> None:
    assert not hasattr(StrategyCandidate, method_name)
    assert not hasattr(StrategyDecision, method_name)
