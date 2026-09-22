from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    Asset,
    Horizon,
    OutcomeSide,
    RiskDecision,
    StrategyCandidate,
    StrategyKind,
)
from direction_engine_v3.risk import (
    LiquidityEvidence,
    OpenExposure,
    PortfolioState,
    RiskPolicy,
    assess_candidate,
)

NOW = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


def candidate(*, capital: str = "10") -> StrategyCandidate:
    return StrategyCandidate(
        "candidate-1",
        StrategyKind.DIRECTIONAL_EDGE,
        "market-1",
        Decimal(capital),
        Decimal("1"),
        Decimal("0.7"),
        NOW,
        NOW + timedelta(seconds=30),
        OutcomeSide.UP,
    )


def liquidity() -> LiquidityEvidence:
    return LiquidityEvidence(
        NOW,
        timedelta(milliseconds=100),
        Decimal("0.02"),
        Decimal("20"),
        Decimal("10"),
        Decimal("0.01"),
        Decimal("0.9"),
        5,
        True,
        True,
        True,
    )


def state(*, exposures: tuple[OpenExposure, ...] = ()) -> PortfolioState:
    return PortfolioState(
        Decimal("100"),
        exposures,
        Decimal("0"),
        Decimal("0"),
        0,
        None,
        False,
        True,
        NOW,
    )


def policy() -> RiskPolicy:
    return RiskPolicy(
        3,
        Decimal("50"),
        Decimal("30"),
        Decimal("30"),
        Decimal("40"),
        2,
        Decimal("10"),
        Decimal("20"),
        Decimal("15"),
        3,
        timedelta(seconds=1),
        timedelta(seconds=1),
        Decimal("0.05"),
        Decimal("0.03"),
        Decimal("0.7"),
        3,
    )


def assess(
    *,
    item: StrategyCandidate | None = None,
    evidence: LiquidityEvidence | None = None,
    portfolio: PortfolioState | None = None,
) -> RiskDecision:
    return assess_candidate(
        item or candidate(),
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        liquidity=liquidity() if evidence is None else evidence,
        state=state() if portfolio is None else portfolio,
        policy=policy(),
        assessed_at=NOW + timedelta(milliseconds=200),
        risk_decision_id="risk-1",
    )


def test_complete_healthy_state_approves_exact_fixed_requested_capital() -> None:
    result = assess()
    assert result.approved
    assert result.approved_capital == Decimal("10")
    assert result.reason_codes == ()


def test_missing_or_unreconciled_state_and_kill_switch_fail_closed() -> None:
    portfolio = replace(
        state(),
        bankroll_available=None,
        exposures=None,
        kill_switch_active=True,
        ledger_reconciled=False,
    )
    result = assess(portfolio=portfolio)
    assert not result.approved
    assert set(result.reason_codes) >= {
        "GLOBAL_KILL_SWITCH",
        "LEDGER_NOT_RECONCILED",
        "PORTFOLIO_STATE_INCOMPLETE",
    }


def test_stale_portfolio_state_fails_closed() -> None:
    portfolio = replace(state(), assessed_at=NOW - timedelta(seconds=2))
    result = assess(portfolio=portfolio)
    assert result.reason_codes == ("PORTFOLIO_STATE_STALE_OR_FUTURE",)


def test_all_exposure_dimensions_include_projected_crypto_capital() -> None:
    exposure = OpenExposure(
        "position-1",
        "market-old",
        Asset.BTC,
        Horizon.FIVE_MINUTES,
        Decimal("41"),
        NOW + timedelta(minutes=4),
    )
    result = assess(portfolio=state(exposures=(exposure,)))
    assert set(result.reason_codes) >= {
        "TOTAL_EXPOSURE_LIMIT",
        "ASSET_EXPOSURE_LIMIT",
        "HORIZON_EXPOSURE_LIMIT",
        "CRYPTO_CLUSTER_EXPOSURE_LIMIT",
    }


def test_position_count_overlap_bankroll_and_directional_cap_are_enforced() -> None:
    exposures = tuple(
        OpenExposure(
            f"position-{index}",
            f"market-{index}",
            Asset.BTC if index < 2 else Asset.ETH,
            Horizon.FIFTEEN_MINUTES,
            Decimal("1"),
            NOW + timedelta(minutes=10),
        )
        for index in range(3)
    )
    portfolio = replace(state(exposures=exposures), bankroll_available=Decimal("5"))
    result = assess(item=candidate(capital="11"), portfolio=portfolio)
    assert set(result.reason_codes) >= {
        "MAXIMUM_POSITIONS",
        "OVERLAPPING_ASSET_LIMIT",
        "INSUFFICIENT_BANKROLL",
        "DIRECTIONAL_FIXED_STAKE_EXCEEDED",
    }


def test_loss_drawdown_and_active_cooldown_block_new_risk() -> None:
    portfolio = replace(
        state(),
        realized_pnl_today=Decimal("-20"),
        drawdown=Decimal("15"),
        consecutive_losses=3,
        cooldown_until=NOW + timedelta(hours=1),
    )
    result = assess(portfolio=portfolio)
    assert set(result.reason_codes) >= {
        "DAILY_LOSS_LIMIT",
        "DRAWDOWN_LIMIT",
        "CONSECUTIVE_LOSS_COOLDOWN_ACTIVE",
    }


def test_expired_or_missing_cooldown_does_not_deadlock_after_loss_streak() -> None:
    expired = replace(
        state(),
        consecutive_losses=3,
        cooldown_until=NOW - timedelta(minutes=1),
    )
    missing = replace(state(), consecutive_losses=3, cooldown_until=None)

    assert "CONSECUTIVE_LOSS_COOLDOWN_ACTIVE" not in assess(
        portfolio=expired
    ).reason_codes
    assert "CONSECUTIVE_LOSS_COOLDOWN_ACTIVE" not in assess(
        portfolio=missing
    ).reason_codes


def test_missing_liquidity_fails_closed() -> None:
    result = assess_candidate(
        candidate(),
        asset=Asset.BTC,
        horizon=Horizon.FIVE_MINUTES,
        liquidity=None,
        state=state(),
        policy=policy(),
        assessed_at=NOW + timedelta(milliseconds=200),
        risk_decision_id="risk-missing",
    )
    assert result.reason_codes == ("LIQUIDITY_EVIDENCE_MISSING",)


def test_liquidity_gates_report_stale_gap_depth_impact_and_transience() -> None:
    evidence = replace(
        liquidity(),
        source_age=timedelta(seconds=2),
        spread=Decimal("0.06"),
        executable_depth=Decimal("9"),
        price_impact=Decimal("0.04"),
        persistence_ratio=Decimal("0.6"),
        sequence_valid=False,
        transport_healthy=False,
        fee_schedule_available=False,
    )
    result = assess(evidence=evidence)
    assert set(result.reason_codes) == {
        "BOOK_STALE_OR_FUTURE",
        "BOOK_TRANSPORT_UNHEALTHY",
        "BOOK_SEQUENCE_INVALID",
        "FEE_SCHEDULE_UNAVAILABLE",
        "SPREAD_TOO_WIDE",
        "INSUFFICIENT_EXECUTABLE_DEPTH",
        "PRICE_IMPACT_TOO_HIGH",
        "TRANSIENT_LIQUIDITY_RISK",
    }


def test_insufficient_depth_history_is_distinct_from_transient_liquidity() -> None:
    result = assess(evidence=replace(liquidity(), persistence_observations=2))
    assert result.reason_codes == ("DEPTH_HISTORY_INSUFFICIENT",)
