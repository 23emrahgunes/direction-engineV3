"""Bounded external temporal state for Directional Edge features."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from direction_engine_v3.domain import Asset, OfficialReference, ProxyReference
from direction_engine_v3.domain._validation import require_utc
from direction_engine_v3.features.directional import ExternalDirectionalSnapshot
from direction_engine_v3.market_data import CryptoTopOfBook, CryptoTrade

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class TemporalFeatureResult:
    snapshot: ExternalDirectionalSnapshot | None
    reason: str


class ExternalTemporalState:
    """Per-asset bounded history; uses event timestamps and never fabricates values."""

    def __init__(self, *, max_age: timedelta, minimum_points: int = 3) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        if minimum_points < 2:
            raise ValueError("minimum_points must be at least two")
        self._max_age = max_age
        self._minimum_points = minimum_points
        self._references: dict[Asset, deque[OfficialReference | ProxyReference]] = {}
        self._books: dict[Asset, deque[CryptoTopOfBook]] = {}
        self._trades: dict[Asset, deque[CryptoTrade]] = {}

    def add_reference(self, reference: OfficialReference | ProxyReference) -> None:
        self._references.setdefault(reference.asset, deque()).append(reference)

    def add_book(self, book: CryptoTopOfBook) -> None:
        self._books.setdefault(book.asset, deque()).append(book)

    def add_trade(self, trade: CryptoTrade) -> None:
        self._trades.setdefault(trade.asset, deque()).append(trade)

    def build_snapshot(
        self,
        *,
        asset: Asset,
        current_reference: OfficialReference | ProxyReference,
        observed_at: datetime,
    ) -> TemporalFeatureResult:
        require_utc("observed_at", observed_at)
        self._prune(asset, observed_at)
        references = tuple(self._references.get(asset, ()))
        books = tuple(self._books.get(asset, ()))
        trades = tuple(self._trades.get(asset, ()))
        if current_reference.source_ts > observed_at:
            return TemporalFeatureResult(None, "FEATURE_SOURCE_IN_FUTURE")
        if observed_at - current_reference.source_ts > self._max_age:
            return TemporalFeatureResult(None, "EXTERNAL_REFERENCE_STALE")
        if len(references) < self._minimum_points:
            return TemporalFeatureResult(None, "FEATURE_HISTORY_WARMING")
        if not books:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE")
        if not trades:
            return TemporalFeatureResult(None, "EXTERNAL_TRADE_FLOW_STALE")

        prices = tuple(item.value for item in references)
        short_return = _return(prices[-2], prices[-1])
        medium_return = _return(prices[0], prices[-1])
        momentum = (
            short_return - _return(prices[-3], prices[-2])
            if len(prices) >= 3
            else short_return
        )
        returns = tuple(_return(left, right) for left, right in pairwise(prices))
        realized_volatility = _mean_abs(returns)
        volatility_acceleration = (
            abs(returns[-1]) - _mean_abs(returns[:-1]) if len(returns) > 1 else _ZERO
        )

        latest_book = books[-1]
        book_source_ts = latest_book.lineage.source_ts or latest_book.lineage.recv_ts
        if book_source_ts > observed_at:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_FUTURE")
        if observed_at - book_source_ts > self._max_age:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE")
        spread_mid = (latest_book.bid_price + latest_book.ask_price) / Decimal("2")
        depth_total = latest_book.bid_quantity + latest_book.ask_quantity
        if depth_total <= _ZERO:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE")
        book_imbalance = (latest_book.bid_quantity - latest_book.ask_quantity) / depth_total
        microprice = (
            latest_book.ask_price * latest_book.bid_quantity
            + latest_book.bid_price * latest_book.ask_quantity
        ) / depth_total
        microprice_distance = (microprice - spread_mid) / spread_mid

        signed_volume = sum(
            (-trade.quantity if trade.buyer_is_maker else trade.quantity) for trade in trades
        )
        total_volume = sum((trade.quantity for trade in trades), _ZERO)
        if total_volume <= _ZERO:
            return TemporalFeatureResult(None, "EXTERNAL_TRADE_FLOW_STALE")
        trade_imbalance = signed_volume / total_volume

        signs = [item > _ZERO for item in returns if item != _ZERO]
        flips = sum(1 for left, right in pairwise(signs) if left != right)
        flip_rate = Decimal(flips) / Decimal(max(len(signs) - 1, 1))
        signal_stability = _ONE - min(_ONE, flip_rate)
        regime_score = min(_ONE, realized_volatility * Decimal("100"))
        return TemporalFeatureResult(
            ExternalDirectionalSnapshot(
                current_reference=current_reference,
                short_return=short_return,
                medium_return=medium_return,
                momentum=momentum,
                realized_volatility=realized_volatility,
                volatility_acceleration=volatility_acceleration,
                spot_perp_basis=_ZERO,
                external_book_imbalance=book_imbalance,
                microprice_distance=microprice_distance,
                trade_imbalance=trade_imbalance,
                signal_stability=signal_stability,
                flip_rate=flip_rate,
                regime_score=regime_score,
                source="external-temporal-state-v1",
                source_ts=max(current_reference.source_ts, book_source_ts),
            ),
            "FEATURES_READY",
        )

    def _prune(self, asset: Asset, observed_at: datetime) -> None:
        for store in (self._references, self._books, self._trades):
            queue = store.setdefault(asset, deque())
            while queue:
                item = queue[0]
                if isinstance(item, OfficialReference | ProxyReference):
                    source_ts = item.source_ts
                else:
                    lineage = item.lineage
                    source_ts = lineage.source_ts or lineage.recv_ts
                if observed_at - source_ts <= self._max_age:
                    break
                queue.popleft()


def _return(previous: Decimal, current: Decimal) -> Decimal:
    if previous <= _ZERO:
        raise ValueError("reference history contains non-positive value")
    return (current - previous) / previous


def _mean_abs(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return _ZERO
    return sum((abs(item) for item in values), _ZERO) / Decimal(len(values))
