from datetime import UTC, datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain import (
    ExecutionPlan,
    OrderIntent,
    OrderSide,
    StrategyKind,
    TimeInForce,
    TradingMode,
)
from direction_engine_v3.execution import LiveSafetyInputs, PreLiveSafetyGate


def _live_plan(now: datetime) -> ExecutionPlan:
    intent = OrderIntent(
        "client-1",
        "candidate-1",
        StrategyKind.DIRECTIONAL_EDGE,
        "market-1",
        "token-1",
        OrderSide.BUY,
        Decimal("1"),
        Decimal("0.50"),
        TimeInForce.GTD,
        now,
        now + timedelta(seconds=30),
    )
    return ExecutionPlan(
        "plan-1",
        "decision-1",
        "risk-1",
        TradingMode.LIVE,
        (intent,),
        "idem-1",
        now,
        now + timedelta(seconds=30),
    )


def _inputs(now: datetime) -> LiveSafetyInputs:
    return LiveSafetyInputs(
        live_trading_enabled=False,
        live_armed=False,
        live_auto_arm=False,
        kill_switch_active=False,
        market_active=True,
        token_mapping_verified=True,
        data_fresh=True,
        risk_approved=True,
        idempotency_key_consumed=False,
        unresolved_order_state=False,
        residual_exposure=False,
        credentials_present=False,
        geo_eligible=False,
        account_eligible=False,
        checked_at=now,
    )


def test_prelive_gate_denies_default_unarmed_live_plan() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = PreLiveSafetyGate().evaluate(_live_plan(now), _inputs(now))

    assert result.approved is False
    assert "LIVE_TRADING_DISABLED" in result.reason_codes
    assert "LIVE_NOT_ARMED" in result.reason_codes
    assert "CREDENTIALS_MISSING" in result.reason_codes
    assert "GEO_ELIGIBILITY_UNVERIFIED" in result.reason_codes


def test_prelive_gate_blocks_auto_arm_and_incident_states() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    inputs = _inputs(now)
    incident_inputs = LiveSafetyInputs(
        live_trading_enabled=True,
        live_armed=True,
        live_auto_arm=True,
        kill_switch_active=False,
        market_active=True,
        token_mapping_verified=True,
        data_fresh=True,
        risk_approved=True,
        idempotency_key_consumed=True,
        unresolved_order_state=True,
        residual_exposure=True,
        credentials_present=True,
        geo_eligible=True,
        account_eligible=True,
        checked_at=inputs.checked_at,
    )

    result = PreLiveSafetyGate().evaluate(_live_plan(now), incident_inputs)

    assert result.approved is False
    assert "LIVE_AUTO_ARM_FORBIDDEN" in result.reason_codes
    assert "IDEMPOTENCY_KEY_CONSUMED" in result.reason_codes
    assert "UNRESOLVED_ORDER_STATE" in result.reason_codes
    assert "RESIDUAL_EXPOSURE" in result.reason_codes


def test_prelive_gate_can_only_approve_when_all_checks_are_explicitly_true() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    inputs = LiveSafetyInputs(
        live_trading_enabled=True,
        live_armed=True,
        live_auto_arm=False,
        kill_switch_active=False,
        market_active=True,
        token_mapping_verified=True,
        data_fresh=True,
        risk_approved=True,
        idempotency_key_consumed=False,
        unresolved_order_state=False,
        residual_exposure=False,
        credentials_present=True,
        geo_eligible=True,
        account_eligible=True,
        checked_at=now,
    )

    result = PreLiveSafetyGate().evaluate(_live_plan(now), inputs)

    assert result.approved is True
    assert result.reason_codes == ()
