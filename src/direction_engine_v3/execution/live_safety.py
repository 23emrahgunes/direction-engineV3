"""PRE-LIVE safety gate models.

This module intentionally contains no Polymarket order submission, signing, or network I/O.
"""

from dataclasses import dataclass
from datetime import datetime

from direction_engine_v3.domain import ExecutionPlan, TradingMode
from direction_engine_v3.domain._validation import require_text, require_utc


@dataclass(frozen=True, slots=True)
class LiveSafetyInputs:
    live_trading_enabled: bool
    live_armed: bool
    live_auto_arm: bool
    kill_switch_active: bool
    market_active: bool
    token_mapping_verified: bool
    data_fresh: bool
    risk_approved: bool
    idempotency_key_consumed: bool
    unresolved_order_state: bool
    residual_exposure: bool
    credentials_present: bool
    geo_eligible: bool
    account_eligible: bool
    checked_at: datetime

    def __post_init__(self) -> None:
        require_utc("checked_at", self.checked_at)


@dataclass(frozen=True, slots=True)
class LiveSafetyResult:
    approved: bool
    reason_codes: tuple[str, ...]
    checked_at: datetime

    def __post_init__(self) -> None:
        require_utc("checked_at", self.checked_at)
        for reason_code in self.reason_codes:
            require_text("reason_code", reason_code)
        if self.approved and self.reason_codes:
            raise ValueError("approved result must not include rejection reasons")
        if not self.approved and not self.reason_codes:
            raise ValueError("denied result requires at least one reason code")


class PreLiveSafetyGate:
    """Validate all preconditions before any future LIVE gateway could submit."""

    def evaluate(self, plan: ExecutionPlan, inputs: LiveSafetyInputs) -> LiveSafetyResult:
        reasons: list[str] = []
        if plan.mode is not TradingMode.LIVE:
            reasons.append("PLAN_NOT_LIVE")
        if not inputs.live_trading_enabled:
            reasons.append("LIVE_TRADING_DISABLED")
        if inputs.live_auto_arm:
            reasons.append("LIVE_AUTO_ARM_FORBIDDEN")
        if not inputs.live_armed:
            reasons.append("LIVE_NOT_ARMED")
        if inputs.kill_switch_active:
            reasons.append("KILL_SWITCH_ACTIVE")
        if not inputs.market_active:
            reasons.append("MARKET_NOT_ACTIVE")
        if not inputs.token_mapping_verified:
            reasons.append("TOKEN_MAPPING_UNVERIFIED")
        if not inputs.data_fresh:
            reasons.append("DATA_NOT_FRESH")
        if not inputs.risk_approved:
            reasons.append("RISK_NOT_APPROVED")
        if inputs.idempotency_key_consumed:
            reasons.append("IDEMPOTENCY_KEY_CONSUMED")
        if inputs.unresolved_order_state:
            reasons.append("UNRESOLVED_ORDER_STATE")
        if inputs.residual_exposure:
            reasons.append("RESIDUAL_EXPOSURE")
        if not inputs.credentials_present:
            reasons.append("CREDENTIALS_MISSING")
        if not inputs.geo_eligible:
            reasons.append("GEO_ELIGIBILITY_UNVERIFIED")
        if not inputs.account_eligible:
            reasons.append("ACCOUNT_ELIGIBILITY_UNVERIFIED")
        if plan.expires_at <= inputs.checked_at:
            reasons.append("PLAN_EXPIRED")
        if reasons:
            return LiveSafetyResult(False, tuple(dict.fromkeys(reasons)), inputs.checked_at)
        return LiveSafetyResult(True, (), inputs.checked_at)
