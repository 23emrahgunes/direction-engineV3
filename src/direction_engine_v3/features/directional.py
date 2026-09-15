"""External-only directional feature construction."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from direction_engine_v3.domain import (
    FeatureValue,
    FeatureVector,
    Market,
    OfficialReference,
    ProxyReference,
)
from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc
from direction_engine_v3.market_data import HORIZON_DURATIONS, PriceToBeatRecord

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ExternalDirectionalSnapshot:
    """Required non-Polymarket predictors at one event-time boundary."""

    current_reference: OfficialReference | ProxyReference
    short_return: Decimal
    medium_return: Decimal
    momentum: Decimal
    realized_volatility: Decimal
    volatility_acceleration: Decimal
    spot_perp_basis: Decimal
    external_book_imbalance: Decimal
    microprice_distance: Decimal
    trade_imbalance: Decimal
    signal_stability: Decimal
    flip_rate: Decimal
    regime_score: Decimal
    source: str
    source_ts: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.current_reference, (OfficialReference, ProxyReference)):
            raise TypeError("current_reference must be an official or proxy reference")
        for name in (
            "short_return",
            "medium_return",
            "momentum",
            "realized_volatility",
            "volatility_acceleration",
            "spot_perp_basis",
            "external_book_imbalance",
            "microprice_distance",
            "trade_imbalance",
            "signal_stability",
            "flip_rate",
            "regime_score",
        ):
            require_decimal(name, getattr(self, name))
        if self.realized_volatility < _ZERO:
            raise ValueError("realized_volatility must be non-negative")
        if not _ZERO <= self.signal_stability <= Decimal("1"):
            raise ValueError("signal_stability must be between zero and one")
        if not _ZERO <= self.flip_rate <= Decimal("1"):
            raise ValueError("flip_rate must be between zero and one")
        require_text("source", self.source)
        require_utc("source_ts", self.source_ts)


def build_directional_features(
    market: Market,
    price_to_beat: PriceToBeatRecord,
    snapshot: ExternalDirectionalSnapshot,
    *,
    generated_at: datetime,
    feature_set_version: str,
) -> FeatureVector:
    """Build a complete external feature vector without CLOB price inputs."""

    require_utc("generated_at", generated_at)
    require_text("feature_set_version", feature_set_version)
    if price_to_beat.reference.market_id != market.market_id:
        raise ValueError("price-to-beat market identity mismatch")
    if price_to_beat.condition_id != market.condition_id:
        raise ValueError("price-to-beat condition identity mismatch")
    reference = snapshot.current_reference
    if reference.market_id != market.market_id or reference.asset is not market.asset:
        raise ValueError("current reference identity mismatch")
    if snapshot.source_ts > generated_at or reference.source_ts > generated_at:
        raise ValueError("external feature data cannot come from the future")
    tte = market.window_end - generated_at
    if tte.total_seconds() <= 0 or generated_at < market.window_start:
        raise ValueError("feature generation requires an active canonical market")
    horizon = HORIZON_DURATIONS[market.horizon]
    ptb_distance = (reference.value - price_to_beat.value) / price_to_beat.value
    values = (
        ("ptb_normalized_distance", ptb_distance),
        ("tte_fraction", Decimal(str(tte / horizon))),
        ("short_return", snapshot.short_return),
        ("medium_return", snapshot.medium_return),
        ("momentum", snapshot.momentum),
        ("realized_volatility", snapshot.realized_volatility),
        ("volatility_acceleration", snapshot.volatility_acceleration),
        ("spot_perp_basis", snapshot.spot_perp_basis),
        ("external_book_imbalance", snapshot.external_book_imbalance),
        ("microprice_distance", snapshot.microprice_distance),
        ("trade_imbalance", snapshot.trade_imbalance),
        ("signal_stability", snapshot.signal_stability),
        ("flip_rate", snapshot.flip_rate),
        ("regime_score", snapshot.regime_score),
    )
    features = tuple(
        FeatureValue(name=name, value=value, source=snapshot.source, source_ts=snapshot.source_ts)
        for name, value in values
    )
    return FeatureVector(
        market_id=market.market_id,
        asset=market.asset,
        horizon=market.horizon,
        feature_set_version=feature_set_version,
        features=features,
        generated_at=generated_at,
    )
