"""Pure restart-safe opportunity arbitration and claim contracts."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from direction_engine_v3.domain import StrategyCandidate, StrategyKind
from direction_engine_v3.domain._validation import (
    require_decimal,
    require_text,
    require_tuple,
    require_unique,
    require_utc,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class ClaimStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RECONCILED = "RECONCILED"


@dataclass(frozen=True, slots=True)
class RoutingOpportunity:
    candidate: StrategyCandidate
    condition_id: str
    token_ids: tuple[str, ...]
    risk_adjusted_score: Decimal
    execution_certainty: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, StrategyCandidate):
            raise TypeError("candidate must be StrategyCandidate")
        require_text("condition_id", self.condition_id)
        require_tuple("token_ids", self.token_ids)
        if not self.token_ids:
            raise ValueError("token_ids must not be empty")
        for token_id in self.token_ids:
            require_text("token_id", token_id)
        require_unique("token_ids", self.token_ids)
        require_decimal(
            "risk_adjusted_score", self.risk_adjusted_score, minimum=_ZERO, maximum=_ONE
        )
        require_decimal(
            "execution_certainty", self.execution_certainty, minimum=_ZERO, maximum=_ONE
        )


@dataclass(frozen=True, slots=True)
class RouterClaim:
    claim_id: str
    idempotency_key: str
    candidate_id: str
    condition_id: str
    token_ids: tuple[str, ...]
    strategy: StrategyKind
    reserved_capital: Decimal
    created_at: datetime
    expires_at: datetime
    status: ClaimStatus = ClaimStatus.ACTIVE
    reconciled_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("claim_id", self.claim_id),
            ("idempotency_key", self.idempotency_key),
            ("candidate_id", self.candidate_id),
            ("condition_id", self.condition_id),
        ):
            require_text(name, value)
        require_tuple("token_ids", self.token_ids)
        if not self.token_ids:
            raise ValueError("token_ids must not be empty")
        require_unique("token_ids", self.token_ids)
        if not isinstance(self.strategy, StrategyKind):
            raise TypeError("strategy must be StrategyKind")
        require_decimal(
            "reserved_capital", self.reserved_capital, minimum=_ZERO, minimum_exclusive=True
        )
        require_utc("created_at", self.created_at)
        require_utc("expires_at", self.expires_at)
        if self.created_at >= self.expires_at:
            raise ValueError("claim creation must precede expiry")
        if not isinstance(self.status, ClaimStatus):
            raise TypeError("status must be ClaimStatus")
        if self.reconciled_at is not None:
            require_utc("reconciled_at", self.reconciled_at)
            if self.reconciled_at < self.created_at:
                raise ValueError("reconciliation cannot precede claim creation")
        if (self.status is ClaimStatus.RECONCILED) != (self.reconciled_at is not None):
            raise ValueError("reconciled status and timestamp must agree")


@dataclass(frozen=True, slots=True)
class RouterSnapshot:
    revision: int
    claims: tuple[RouterClaim, ...]
    unresolved_structural_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("revision must be a non-negative integer")
        require_tuple("claims", self.claims)
        if any(not isinstance(item, RouterClaim) for item in self.claims):
            raise TypeError("claims must contain RouterClaim values")
        require_unique("claim IDs", (item.claim_id for item in self.claims))
        require_unique("idempotency keys", (item.idempotency_key for item in self.claims))
        require_tuple("unresolved_structural_conditions", self.unresolved_structural_conditions)
        require_unique(
            "unresolved structural conditions", self.unresolved_structural_conditions
        )


@dataclass(frozen=True, slots=True)
class RouterPolicy:
    policy_version: str
    strategy_priority: tuple[StrategyKind, ...]
    dual40_research_enabled: bool

    def __post_init__(self) -> None:
        require_text("policy_version", self.policy_version)
        require_tuple("strategy_priority", self.strategy_priority)
        if set(self.strategy_priority) != set(StrategyKind):
            raise ValueError("strategy_priority must contain every strategy exactly once")
        if not isinstance(self.dual40_research_enabled, bool):
            raise TypeError("dual40_research_enabled must be bool")


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    selected: RoutingOpportunity | None
    claim: RouterClaim | None
    reason: str
    rejected: tuple[tuple[str, str], ...]
    expected_revision: int
    policy_version: str


def route_opportunities(
    opportunities: tuple[RoutingOpportunity, ...],
    *,
    snapshot: RouterSnapshot,
    available_capital: Decimal | None,
    policy: RouterPolicy,
    now: datetime,
) -> RoutingDecision:
    """Select one opportunity and emit a claim for atomic compare-and-set persistence."""

    require_tuple("opportunities", opportunities)
    require_utc("now", now)
    if available_capital is None:
        return _none(snapshot, policy, "CAPITAL_STATE_UNAVAILABLE", ())
    require_decimal("available_capital", available_capital, minimum=_ZERO)
    unreconciled = tuple(
        claim for claim in snapshot.claims if claim.status is ClaimStatus.ACTIVE
    )
    if any(claim.expires_at <= now for claim in unreconciled):
        return _none(snapshot, policy, "CLAIM_RECONCILIATION_REQUIRED", ())
    rejected: list[tuple[str, str]] = []
    eligible = []
    for item in opportunities:
        reason = _conflict_reason(item, snapshot, unreconciled, policy, now)
        if reason is None:
            eligible.append(item)
        else:
            rejected.append((item.candidate.candidate_id, reason))
    if not eligible:
        return _none(snapshot, policy, "NO_ELIGIBLE_OPPORTUNITY", tuple(rejected))
    reserved = sum((claim.reserved_capital for claim in unreconciled), _ZERO)
    remaining = available_capital - reserved
    funded = [item for item in eligible if item.candidate.required_capital <= remaining]
    for item in eligible:
        if item not in funded:
            rejected.append((item.candidate.candidate_id, "CAPITAL_ALREADY_CLAIMED"))
    if not funded:
        return _none(snapshot, policy, "INSUFFICIENT_UNCLAIMED_CAPITAL", tuple(rejected))
    priority = {strategy: index for index, strategy in enumerate(policy.strategy_priority)}
    selected = min(
        funded,
        key=lambda item: (
            priority[item.candidate.strategy],
            -item.risk_adjusted_score,
            -item.execution_certainty,
            -item.candidate.expected_net_value,
            item.candidate.candidate_id,
        ),
    )
    claim = RouterClaim(
        claim_id=f"claim:{selected.candidate.candidate_id}",
        idempotency_key=f"route:{selected.candidate.candidate_id}",
        candidate_id=selected.candidate.candidate_id,
        condition_id=selected.condition_id,
        token_ids=selected.token_ids,
        strategy=selected.candidate.strategy,
        reserved_capital=selected.candidate.required_capital,
        created_at=now,
        expires_at=selected.candidate.expires_at,
    )
    return RoutingDecision(
        selected,
        claim,
        "CLAIM_PROPOSED",
        tuple(rejected),
        snapshot.revision,
        policy.policy_version,
    )


def reconcile_claim(
    snapshot: RouterSnapshot, *, claim_id: str, reconciled_at: datetime
) -> RouterSnapshot:
    """Close one persisted claim only after execution state has been reconciled."""

    require_text("claim_id", claim_id)
    require_utc("reconciled_at", reconciled_at)
    matches = [claim for claim in snapshot.claims if claim.claim_id == claim_id]
    if not matches:
        raise KeyError(claim_id)
    claim = matches[0]
    if claim.status is ClaimStatus.RECONCILED:
        return snapshot
    updated = tuple(
        replace(claim, status=ClaimStatus.RECONCILED, reconciled_at=reconciled_at)
        if item.claim_id == claim_id
        else item
        for item in snapshot.claims
    )
    return RouterSnapshot(
        snapshot.revision + 1, updated, snapshot.unresolved_structural_conditions
    )


def _conflict_reason(
    item: RoutingOpportunity,
    snapshot: RouterSnapshot,
    unreconciled: tuple[RouterClaim, ...],
    policy: RouterPolicy,
    now: datetime,
) -> str | None:
    if item.candidate.expires_at <= now:
        return "CANDIDATE_EXPIRED"
    if item.candidate.strategy is StrategyKind.DUAL40 and not policy.dual40_research_enabled:
        return "DUAL40_DISABLED"
    if any(claim.candidate_id == item.candidate.candidate_id for claim in snapshot.claims):
        return "DUPLICATE_CANDIDATE"
    if item.condition_id in snapshot.unresolved_structural_conditions:
        return "UNRESOLVED_STRUCTURAL_ONE_LEG"
    if any(claim.condition_id == item.condition_id for claim in unreconciled):
        return "CONDITION_ALREADY_CLAIMED"
    if any(set(claim.token_ids) & set(item.token_ids) for claim in unreconciled):
        return "TOKEN_ALREADY_CLAIMED"
    return None


def _none(
    snapshot: RouterSnapshot,
    policy: RouterPolicy,
    reason: str,
    rejected: tuple[tuple[str, str], ...],
) -> RoutingDecision:
    return RoutingDecision(None, None, reason, rejected, snapshot.revision, policy.policy_version)
