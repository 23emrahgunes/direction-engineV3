"""Pure PAPER execution-plan builders for routed and risk-approved candidates."""

from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain import (
    ExecutionPlan,
    Market,
    OrderIntent,
    OrderSide,
    OutcomeSide,
    RiskDecision,
    StrategyCandidate,
    StrategyKind,
    TimeInForce,
    TradingMode,
)
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.strategies.structural_arb import StructuralOpportunity

_ZERO = Decimal("0")
_ONE = Decimal("1")


def build_directional_paper_plan(
    candidate: StrategyCandidate,
    market: Market,
    risk: RiskDecision,
    *,
    decision_id: str,
    quantity: Decimal,
    limit_price: Decimal,
    created_at: datetime,
) -> ExecutionPlan:
    """Build a one-leg PAPER plan after router/risk approval; never submits it."""

    require_text("decision_id", decision_id)
    require_utc("created_at", created_at)
    require_decimal("quantity", quantity, minimum=_ZERO, minimum_exclusive=True)
    require_decimal("limit_price", limit_price, minimum=_ZERO, maximum=_ONE)
    _require_approved(candidate, risk)
    if candidate.strategy is not StrategyKind.DIRECTIONAL_EDGE:
        raise ValueError("directional PAPER plan requires a Directional Edge candidate")
    if candidate.outcome_side is None:
        raise ValueError("directional PAPER plan requires an outcome side")
    token_id = _token_id(market, candidate.outcome_side)
    client_order_id = f"paper:{candidate.candidate_id}:BUY:{token_id}"
    intent = OrderIntent(
        client_order_id,
        candidate.candidate_id,
        StrategyKind.DIRECTIONAL_EDGE,
        market.market_id,
        token_id,
        OrderSide.BUY,
        quantity,
        limit_price,
        TimeInForce.FAK,
        created_at,
        candidate.expires_at,
    )
    return _plan(
        candidate,
        risk,
        decision_id=decision_id,
        plan_id=f"plan:{candidate.candidate_id}",
        intents=(intent,),
        created_at=created_at,
    )


def build_structural_buy_merge_paper_plan(
    candidate: StrategyCandidate,
    market: Market,
    opportunity: StructuralOpportunity,
    risk: RiskDecision,
    *,
    decision_id: str,
    created_at: datetime,
) -> ExecutionPlan:
    """Build a two-leg BUY+MERGE PAPER plan from an executable structural opportunity."""

    require_text("decision_id", decision_id)
    require_utc("created_at", created_at)
    _require_approved(candidate, risk)
    if candidate.strategy is not StrategyKind.STRUCTURAL_ARBITRAGE:
        raise ValueError("structural PAPER plan requires a Structural Arbitrage candidate")
    if opportunity.condition_id != market.condition_id or opportunity.market_id != market.market_id:
        raise ValueError("structural opportunity identity mismatch")
    intents = (
        OrderIntent(
            f"paper:{candidate.candidate_id}:BUY:{opportunity.up.token_id}",
            candidate.candidate_id,
            StrategyKind.STRUCTURAL_ARBITRAGE,
            market.market_id,
            opportunity.up.token_id,
            OrderSide.BUY,
            opportunity.shares,
            opportunity.up.worst_price or Decimal("1"),
            TimeInForce.FAK,
            created_at,
            candidate.expires_at,
        ),
        OrderIntent(
            f"paper:{candidate.candidate_id}:BUY:{opportunity.down.token_id}",
            candidate.candidate_id,
            StrategyKind.STRUCTURAL_ARBITRAGE,
            market.market_id,
            opportunity.down.token_id,
            OrderSide.BUY,
            opportunity.shares,
            opportunity.down.worst_price or Decimal("1"),
            TimeInForce.FAK,
            created_at,
            candidate.expires_at,
        ),
    )
    return _plan(
        candidate,
        risk,
        decision_id=decision_id,
        plan_id=f"plan:{candidate.candidate_id}",
        intents=intents,
        created_at=created_at,
    )


def _plan(
    candidate: StrategyCandidate,
    risk: RiskDecision,
    *,
    decision_id: str,
    plan_id: str,
    intents: tuple[OrderIntent, ...],
    created_at: datetime,
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id,
        decision_id,
        risk.risk_decision_id,
        TradingMode.PAPER,
        intents,
        f"paper-plan:{candidate.candidate_id}",
        created_at,
        candidate.expires_at,
    )


def _require_approved(candidate: StrategyCandidate, risk: RiskDecision) -> None:
    if risk.candidate_id != candidate.candidate_id:
        raise ValueError("risk decision candidate mismatch")
    if not risk.approved:
        raise ValueError("cannot build an execution plan from denied risk")


def _token_id(market: Market, side: OutcomeSide) -> str:
    matches = [token.token_id for token in market.tokens if token.outcome is side]
    if len(matches) != 1:
        raise ValueError("market token mapping is not exactly one token for side")
    return matches[0]
