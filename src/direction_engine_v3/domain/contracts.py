"""Pure immutable domain contracts for direction-engineV3."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from direction_engine_v3.domain._validation import (
    require_decimal,
    require_text,
    require_time_order,
    require_tuple,
    require_unique,
    require_utc,
)
from direction_engine_v3.domain.enums import (
    Asset,
    DecisionAction,
    Horizon,
    OrderSide,
    OrderStatus,
    OutcomeSide,
    ReferenceKind,
    StrategyKind,
    TimeInForce,
    TradingMode,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class MarketToken:
    """Stable identity for one outcome token."""

    token_id: str
    outcome: OutcomeSide

    def __post_init__(self) -> None:
        require_text("token_id", self.token_id)
        if not isinstance(self.outcome, OutcomeSide):
            raise TypeError("outcome must be an OutcomeSide")


@dataclass(frozen=True, slots=True)
class Market:
    """Canonical binary market identity and settlement window."""

    market_id: str
    condition_id: str
    asset: Asset
    horizon: Horizon
    tokens: tuple[MarketToken, ...]
    window_start: datetime
    window_end: datetime
    settlement_source: str

    def __post_init__(self) -> None:
        require_text("market_id", self.market_id)
        require_text("condition_id", self.condition_id)
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")
        require_tuple("tokens", self.tokens)
        invalid_tokens = len(self.tokens) != 2 or any(
            not isinstance(token, MarketToken) for token in self.tokens
        )
        if invalid_tokens:
            raise ValueError("tokens must contain exactly two MarketToken values")
        require_unique("token IDs", (token.token_id for token in self.tokens))
        if {token.outcome for token in self.tokens} != {OutcomeSide.UP, OutcomeSide.DOWN}:
            raise ValueError("tokens must map exactly one UP and one DOWN outcome")
        require_time_order(
            "window_start",
            self.window_start,
            "window_end",
            self.window_end,
            allow_equal=False,
        )
        expected_duration = {
            Horizon.FIVE_MINUTES: timedelta(minutes=5),
            Horizon.FIFTEEN_MINUTES: timedelta(minutes=15),
            Horizon.ONE_HOUR: timedelta(hours=1),
        }[self.horizon]
        if self.window_end - self.window_start != expected_duration:
            raise ValueError("market window duration must exactly match horizon")
        duration_seconds = int(expected_duration.total_seconds())
        if int(self.window_start.timestamp()) % duration_seconds != 0:
            raise ValueError("market window start must align to its canonical UTC boundary")
        if self.window_start.microsecond != 0:
            raise ValueError("market window start must not contain fractional seconds")
        require_text("settlement_source", self.settlement_source)


@dataclass(frozen=True, slots=True)
class OfficialReference:
    """Authoritative reference used by market rules or settlement."""

    reference_id: str
    market_id: str
    asset: Asset
    value: Decimal
    source: str
    source_ts: datetime
    recv_ts: datetime
    effective_ts: datetime
    is_price_to_beat: bool
    kind: ReferenceKind = field(default=ReferenceKind.OFFICIAL, init=False)

    def __post_init__(self) -> None:
        _validate_reference_fields(
            self.reference_id,
            self.market_id,
            self.asset,
            self.value,
            self.source,
            self.source_ts,
            self.recv_ts,
            self.effective_ts,
        )
        if not isinstance(self.is_price_to_beat, bool):
            raise TypeError("is_price_to_beat must be a bool")


@dataclass(frozen=True, slots=True)
class ProxyReference:
    """Fast external reference used as predictive information only."""

    reference_id: str
    market_id: str
    asset: Asset
    value: Decimal
    source: str
    source_ts: datetime
    recv_ts: datetime
    effective_ts: datetime
    kind: ReferenceKind = field(default=ReferenceKind.PROXY, init=False)

    def __post_init__(self) -> None:
        _validate_reference_fields(
            self.reference_id,
            self.market_id,
            self.asset,
            self.value,
            self.source,
            self.source_ts,
            self.recv_ts,
            self.effective_ts,
        )


def _validate_reference_fields(
    reference_id: str,
    market_id: str,
    asset: Asset,
    value: Decimal,
    source: str,
    source_ts: datetime,
    recv_ts: datetime,
    effective_ts: datetime,
) -> None:
    require_text("reference_id", reference_id)
    require_text("market_id", market_id)
    if not isinstance(asset, Asset):
        raise TypeError("asset must be an Asset")
    require_decimal("value", value, minimum=_ZERO, minimum_exclusive=True)
    require_text("source", source)
    require_utc("source_ts", source_ts)
    require_utc("recv_ts", recv_ts)
    require_utc("effective_ts", effective_ts)


@dataclass(frozen=True, slots=True)
class BookLevel:
    """One immutable prediction-market price level."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        require_decimal("price", self.price, minimum=_ZERO, maximum=_ONE)
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Canonical point-in-time CLOB snapshot."""

    market_id: str
    token_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    source: str
    source_ts: datetime
    recv_ts: datetime
    sequence: int | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        require_text("market_id", self.market_id)
        require_text("token_id", self.token_id)
        require_tuple("bids", self.bids)
        require_tuple("asks", self.asks)
        if any(not isinstance(level, BookLevel) for level in (*self.bids, *self.asks)):
            raise TypeError("bids and asks must contain only BookLevel values")
        if tuple(sorted(self.bids, key=lambda level: level.price, reverse=True)) != self.bids:
            raise ValueError("bids must be sorted by descending price")
        if tuple(sorted(self.asks, key=lambda level: level.price)) != self.asks:
            raise ValueError("asks must be sorted by ascending price")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("order book must not be locked or crossed")
        require_text("source", self.source)
        require_utc("source_ts", self.source_ts)
        require_utc("recv_ts", self.recv_ts)
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("sequence must be a non-negative integer or None")
        if self.checksum is not None:
            require_text("checksum", self.checksum)


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """One feature with its own source lineage and event time."""

    name: str
    value: Decimal
    source: str
    source_ts: datetime

    def __post_init__(self) -> None:
        require_text("name", self.name)
        require_decimal("value", self.value)
        require_text("source", self.source)
        require_utc("source_ts", self.source_ts)


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Immutable decision-time feature snapshot."""

    market_id: str
    asset: Asset
    horizon: Horizon
    feature_set_version: str
    features: tuple[FeatureValue, ...]
    generated_at: datetime

    def __post_init__(self) -> None:
        require_text("market_id", self.market_id)
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")
        require_text("feature_set_version", self.feature_set_version)
        require_tuple("features", self.features)
        if any(not isinstance(feature, FeatureValue) for feature in self.features):
            raise TypeError("features must contain only FeatureValue values")
        require_unique("feature names", (feature.name for feature in self.features))
        require_utc("generated_at", self.generated_at)
        if any(feature.source_ts > self.generated_at for feature in self.features):
            raise ValueError("feature source timestamps cannot be after generated_at")


@dataclass(frozen=True, slots=True)
class ProbabilityForecast:
    """Calibrated binary probability forecast."""

    market_id: str
    asset: Asset
    horizon: Horizon
    p_up: Decimal
    p_down: Decimal
    model_version: str
    calibration_version: str
    feature_set_version: str
    feature_as_of: datetime
    generated_at: datetime

    def __post_init__(self) -> None:
        require_text("market_id", self.market_id)
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be an Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon")
        require_decimal("p_up", self.p_up, minimum=_ZERO, maximum=_ONE)
        require_decimal("p_down", self.p_down, minimum=_ZERO, maximum=_ONE)
        if self.p_up + self.p_down != _ONE:
            raise ValueError("p_up and p_down must sum exactly to 1")
        require_text("model_version", self.model_version)
        require_text("calibration_version", self.calibration_version)
        require_text("feature_set_version", self.feature_set_version)
        require_time_order(
            "feature_as_of",
            self.feature_as_of,
            "generated_at",
            self.generated_at,
        )


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """Auditable opportunity proposed by one strategy."""

    candidate_id: str
    strategy: StrategyKind
    market_id: str
    required_capital: Decimal
    expected_net_value: Decimal
    quality_score: Decimal
    created_at: datetime
    expires_at: datetime
    outcome_side: OutcomeSide | None = None

    def __post_init__(self) -> None:
        require_text("candidate_id", self.candidate_id)
        if not isinstance(self.strategy, StrategyKind):
            raise TypeError("strategy must be a StrategyKind")
        require_text("market_id", self.market_id)
        require_decimal(
            "required_capital",
            self.required_capital,
            minimum=_ZERO,
            minimum_exclusive=True,
        )
        require_decimal("expected_net_value", self.expected_net_value)
        require_decimal("quality_score", self.quality_score, minimum=_ZERO, maximum=_ONE)
        require_time_order(
            "created_at",
            self.created_at,
            "expires_at",
            self.expires_at,
            allow_equal=False,
        )
        if self.outcome_side is not None and not isinstance(self.outcome_side, OutcomeSide):
            raise TypeError("outcome_side must be an OutcomeSide or None")
        if self.strategy is StrategyKind.DIRECTIONAL_EDGE and self.outcome_side is None:
            raise ValueError("Directional Edge candidates require an outcome_side")
        if self.strategy is StrategyKind.STRUCTURAL_ARBITRAGE and self.outcome_side is not None:
            raise ValueError("Structural Arbitrage candidates cannot carry a directional outcome")


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """Final pure strategy decision, including first-class abstention."""

    decision_id: str
    strategy: StrategyKind
    market_id: str
    action: DecisionAction
    decided_at: datetime
    reason: str
    candidate: StrategyCandidate | None = None

    def __post_init__(self) -> None:
        require_text("decision_id", self.decision_id)
        if not isinstance(self.strategy, StrategyKind):
            raise TypeError("strategy must be a StrategyKind")
        require_text("market_id", self.market_id)
        if not isinstance(self.action, DecisionAction):
            raise TypeError("action must be a DecisionAction")
        require_utc("decided_at", self.decided_at)
        require_text("reason", self.reason)
        if self.candidate is not None:
            if not isinstance(self.candidate, StrategyCandidate):
                raise TypeError("candidate must be a StrategyCandidate or None")
            if self.candidate.strategy is not self.strategy:
                raise ValueError("candidate strategy must match decision strategy")
            if self.candidate.market_id != self.market_id:
                raise ValueError("candidate market must match decision market")
        if self.action is DecisionAction.TRADE:
            if self.candidate is None:
                raise ValueError("TRADE decisions require a candidate")
            if self.decided_at > self.candidate.expires_at:
                raise ValueError("TRADE decisions cannot use expired candidates")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Fail-closed risk assessment snapshot."""

    risk_decision_id: str
    candidate_id: str
    approved: bool
    approved_capital: Decimal
    assessed_at: datetime
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text("risk_decision_id", self.risk_decision_id)
        require_text("candidate_id", self.candidate_id)
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a bool")
        require_decimal("approved_capital", self.approved_capital, minimum=_ZERO)
        require_utc("assessed_at", self.assessed_at)
        require_tuple("reason_codes", self.reason_codes)
        for reason_code in self.reason_codes:
            require_text("reason_code", reason_code)
        require_unique("reason_codes", self.reason_codes)
        if self.approved and self.approved_capital <= _ZERO:
            raise ValueError("approved decisions require positive approved_capital")
        if not self.approved and self.approved_capital != _ZERO:
            raise ValueError("denied decisions must approve zero capital")
        if not self.approved and not self.reason_codes:
            raise ValueError("denied decisions require at least one reason code")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Side-effect-free intent for one order leg."""

    client_order_id: str
    candidate_id: str
    strategy: StrategyKind
    market_id: str
    token_id: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    time_in_force: TimeInForce
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_text("client_order_id", self.client_order_id)
        require_text("candidate_id", self.candidate_id)
        if not isinstance(self.strategy, StrategyKind):
            raise TypeError("strategy must be a StrategyKind")
        require_text("market_id", self.market_id)
        require_text("token_id", self.token_id)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide")
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("limit_price", self.limit_price, minimum=_ZERO, maximum=_ONE)
        if not isinstance(self.time_in_force, TimeInForce):
            raise TypeError("time_in_force must be a TimeInForce")
        require_time_order(
            "created_at",
            self.created_at,
            "expires_at",
            self.expires_at,
            allow_equal=False,
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable plan shared by PAPER and future guarded-LIVE gateways."""

    plan_id: str
    decision_id: str
    risk_decision_id: str
    mode: TradingMode
    intents: tuple[OrderIntent, ...]
    idempotency_key: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_text("plan_id", self.plan_id)
        require_text("decision_id", self.decision_id)
        require_text("risk_decision_id", self.risk_decision_id)
        if not isinstance(self.mode, TradingMode):
            raise TypeError("mode must be a TradingMode")
        require_tuple("intents", self.intents)
        if not self.intents or any(not isinstance(intent, OrderIntent) for intent in self.intents):
            raise ValueError("intents must contain at least one OrderIntent")
        require_unique("client order IDs", (intent.client_order_id for intent in self.intents))
        require_text("idempotency_key", self.idempotency_key)
        require_time_order(
            "created_at",
            self.created_at,
            "expires_at",
            self.expires_at,
            allow_equal=False,
        )
        if any(intent.expires_at > self.expires_at for intent in self.intents):
            raise ValueError("intent expiry cannot be after plan expiry")


@dataclass(frozen=True, slots=True)
class OrderState:
    """Reconciled point-in-time order state."""

    client_order_id: str
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    created_at: datetime
    updated_at: datetime
    exchange_order_id: str | None = None
    acknowledged_at: datetime | None = None
    average_fill_price: Decimal | None = None

    def __post_init__(self) -> None:
        require_text("client_order_id", self.client_order_id)
        if not isinstance(self.status, OrderStatus):
            raise TypeError("status must be an OrderStatus")
        require_decimal(
            "requested_quantity",
            self.requested_quantity,
            minimum=_ZERO,
            minimum_exclusive=True,
        )
        require_decimal("filled_quantity", self.filled_quantity, minimum=_ZERO)
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity cannot exceed requested_quantity")
        require_time_order("created_at", self.created_at, "updated_at", self.updated_at)
        if self.exchange_order_id is not None:
            require_text("exchange_order_id", self.exchange_order_id)
        if self.acknowledged_at is not None:
            require_time_order(
                "created_at",
                self.created_at,
                "acknowledged_at",
                self.acknowledged_at,
            )
            if self.acknowledged_at > self.updated_at:
                raise ValueError("acknowledged_at cannot be after updated_at")
        if self.average_fill_price is not None:
            require_decimal(
                "average_fill_price",
                self.average_fill_price,
                minimum=_ZERO,
                maximum=_ONE,
            )
        self._validate_status_quantities()

    def _validate_status_quantities(self) -> None:
        no_fill_statuses = {
            OrderStatus.CREATED,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
        if self.status in no_fill_statuses and self.filled_quantity != _ZERO:
            raise ValueError(f"{self.status} cannot have filled quantity")
        if self.status is OrderStatus.PARTIALLY_FILLED and not (
            _ZERO < self.filled_quantity < self.requested_quantity
        ):
            raise ValueError("PARTIALLY_FILLED requires a partial quantity")
        if self.status is OrderStatus.FILLED and self.filled_quantity != self.requested_quantity:
            raise ValueError("FILLED requires the complete requested quantity")
        if self.filled_quantity > _ZERO and self.average_fill_price is None:
            raise ValueError("filled quantities require average_fill_price")
        if self.filled_quantity == _ZERO and self.average_fill_price is not None:
            raise ValueError("average_fill_price requires a filled quantity")
        exchange_identity_statuses = {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }
        if self.status in exchange_identity_statuses and self.exchange_order_id is None:
            raise ValueError(f"{self.status} requires exchange_order_id")
        if self.status is OrderStatus.ACKNOWLEDGED and self.acknowledged_at is None:
            raise ValueError("ACKNOWLEDGED requires acknowledged_at")


@dataclass(frozen=True, slots=True)
class Fill:
    """One venue-reported fill event."""

    fill_id: str
    exchange_order_id: str
    client_order_id: str
    token_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    source_ts: datetime
    recv_ts: datetime

    def __post_init__(self) -> None:
        require_text("fill_id", self.fill_id)
        require_text("exchange_order_id", self.exchange_order_id)
        require_text("client_order_id", self.client_order_id)
        require_text("token_id", self.token_id)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide")
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("price", self.price, minimum=_ZERO, maximum=_ONE)
        require_decimal("fee", self.fee, minimum=_ZERO)
        require_utc("source_ts", self.source_ts)
        require_utc("recv_ts", self.recv_ts)


@dataclass(frozen=True, slots=True)
class Position:
    """Reconciled immutable position snapshot."""

    position_id: str
    market_id: str
    token_id: str
    outcome: OutcomeSide
    quantity: Decimal
    average_price: Decimal
    realized_pnl: Decimal
    opened_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_text("position_id", self.position_id)
        require_text("market_id", self.market_id)
        require_text("token_id", self.token_id)
        if not isinstance(self.outcome, OutcomeSide):
            raise TypeError("outcome must be an OutcomeSide")
        require_decimal("quantity", self.quantity, minimum=_ZERO, minimum_exclusive=True)
        require_decimal("average_price", self.average_price, minimum=_ZERO, maximum=_ONE)
        require_decimal("realized_pnl", self.realized_pnl)
        require_time_order("opened_at", self.opened_at, "updated_at", self.updated_at)


@dataclass(frozen=True, slots=True)
class Settlement:
    """Official market settlement snapshot."""

    settlement_id: str
    market_id: str
    winning_outcome: OutcomeSide
    payout_per_share: Decimal
    source: str
    source_ts: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        require_text("settlement_id", self.settlement_id)
        require_text("market_id", self.market_id)
        if not isinstance(self.winning_outcome, OutcomeSide):
            raise TypeError("winning_outcome must be an OutcomeSide")
        require_decimal(
            "payout_per_share",
            self.payout_per_share,
            minimum=_ZERO,
            maximum=_ONE,
        )
        require_text("source", self.source)
        require_utc("source_ts", self.source_ts)
        require_utc("recorded_at", self.recorded_at)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Auditable immutable ledger event."""

    entry_id: str
    event_type: str
    reference_id: str
    account: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    recorded_at: datetime
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        require_text("entry_id", self.entry_id)
        require_text("event_type", self.event_type)
        require_text("reference_id", self.reference_id)
        require_text("account", self.account)
        require_decimal("amount", self.amount)
        require_text("currency", self.currency)
        require_utc("occurred_at", self.occurred_at)
        require_utc("recorded_at", self.recorded_at)
        require_tuple("metadata", self.metadata)
        for item in self.metadata:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("metadata entries must be key/value tuples")
            require_text("metadata key", item[0])
            require_text("metadata value", item[1])
        require_unique("metadata keys", (item[0] for item in self.metadata))
