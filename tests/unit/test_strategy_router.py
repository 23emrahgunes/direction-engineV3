from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import OutcomeSide, StrategyCandidate, StrategyKind
from direction_engine_v3.router import (
    ClaimStatus,
    RouterClaim,
    RouterPolicy,
    RouterSnapshot,
    RoutingOpportunity,
    reconcile_claim,
    route_opportunities,
)

NOW = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)


def candidate(
    identifier: str,
    strategy: StrategyKind,
    *,
    capital: str = "5",
    expires_at: datetime | None = None,
) -> StrategyCandidate:
    return StrategyCandidate(
        identifier,
        strategy,
        f"market-{identifier}",
        Decimal(capital),
        Decimal("1"),
        Decimal("0.8"),
        NOW,
        expires_at or NOW + timedelta(seconds=10),
        OutcomeSide.UP if strategy is StrategyKind.DIRECTIONAL_EDGE else None,
    )


def opportunity(
    identifier: str,
    strategy: StrategyKind = StrategyKind.DIRECTIONAL_EDGE,
    *,
    condition: str = "condition-1",
    token: str = "token-1",
    capital: str = "5",
    score: str = "0.5",
) -> RoutingOpportunity:
    return RoutingOpportunity(
        candidate(identifier, strategy, capital=capital),
        condition,
        (token,),
        Decimal(score),
        Decimal("0.9"),
    )


def policy(*, dual40: bool = False) -> RouterPolicy:
    return RouterPolicy(
        "router-v1",
        (
            StrategyKind.STRUCTURAL_ARBITRAGE,
            StrategyKind.DIRECTIONAL_EDGE,
            StrategyKind.DUAL40,
        ),
        dual40,
    )


def snapshot(*claims: RouterClaim, unresolved: tuple[str, ...] = ()) -> RouterSnapshot:
    return RouterSnapshot(7, claims, unresolved)


def claim(
    *,
    candidate_id: str = "existing",
    condition: str = "condition-old",
    token: str = "token-old",
    expires_at: datetime | None = None,
) -> RouterClaim:
    return RouterClaim(
        f"claim:{candidate_id}",
        f"route:{candidate_id}",
        candidate_id,
        condition,
        (token,),
        StrategyKind.STRUCTURAL_ARBITRAGE,
        Decimal("6"),
        NOW,
        expires_at or NOW + timedelta(seconds=10),
    )


def test_router_selects_by_versioned_strategy_priority_and_emits_cas_claim() -> None:
    directional = opportunity("directional", score="0.9")
    structural = opportunity(
        "structural",
        StrategyKind.STRUCTURAL_ARBITRAGE,
        condition="condition-2",
        token="token-2",
        score="0.1",
    )
    result = route_opportunities(
        (directional, structural),
        snapshot=snapshot(),
        available_capital=Decimal("20"),
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.selected == structural
    assert result.claim is not None
    assert result.claim.strategy is StrategyKind.STRUCTURAL_ARBITRAGE
    assert result.expected_revision == 7
    assert result.policy_version == "router-v1"


def test_condition_token_and_capital_claims_prevent_races_and_double_spend() -> None:
    existing = claim(condition="condition-1", token="token-old")
    condition_conflict = opportunity("condition-race", token="token-new")
    token_conflict = opportunity(
        "token-race", condition="condition-new", token="token-old"
    )
    unfunded = opportunity(
        "unfunded", condition="condition-free", token="token-free", capital="5"
    )
    result = route_opportunities(
        (condition_conflict, token_conflict, unfunded),
        snapshot=snapshot(existing),
        available_capital=Decimal("10"),
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.reason == "INSUFFICIENT_UNCLAIMED_CAPITAL"
    assert dict(result.rejected) == {
        "condition-race": "CONDITION_ALREADY_CLAIMED",
        "token-race": "TOKEN_ALREADY_CLAIMED",
        "unfunded": "CAPITAL_ALREADY_CLAIMED",
    }


def test_duplicate_candidate_is_rejected_after_restart() -> None:
    existing = claim(candidate_id="same")
    result = route_opportunities(
        (opportunity("same"),),
        snapshot=snapshot(existing),
        available_capital=Decimal("20"),
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.rejected == (("same", "DUPLICATE_CANDIDATE"),)


def test_expired_unreconciled_claim_never_releases_capital_automatically() -> None:
    expired = claim(expires_at=NOW + timedelta(milliseconds=1))
    result = route_opportunities(
        (opportunity("new"),),
        snapshot=snapshot(expired),
        available_capital=Decimal("100"),
        policy=policy(),
        now=NOW + timedelta(seconds=1),
    )
    assert result.reason == "CLAIM_RECONCILIATION_REQUIRED"

    reconciled = reconcile_claim(
        snapshot(expired),
        claim_id=expired.claim_id,
        reconciled_at=NOW + timedelta(seconds=1),
    )
    assert reconciled.revision == 8
    assert reconciled.claims[0].status is ClaimStatus.RECONCILED


def test_unresolved_structural_one_leg_blocks_same_condition() -> None:
    result = route_opportunities(
        (opportunity("directional"),),
        snapshot=snapshot(unresolved=("condition-1",)),
        available_capital=Decimal("100"),
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.rejected == (("directional", "UNRESOLVED_STRUCTURAL_ONE_LEG"),)


def test_dual40_is_research_disabled_by_default() -> None:
    result = route_opportunities(
        (opportunity("dual", StrategyKind.DUAL40),),
        snapshot=snapshot(),
        available_capital=Decimal("100"),
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.rejected == (("dual", "DUAL40_DISABLED"),)


def test_missing_capital_state_fails_closed() -> None:
    result = route_opportunities(
        (opportunity("candidate"),),
        snapshot=snapshot(),
        available_capital=None,
        policy=policy(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert result.reason == "CAPITAL_STATE_UNAVAILABLE"
    assert result.claim is None
