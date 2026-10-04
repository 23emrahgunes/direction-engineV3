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
    diagnostics: dict[str, object]


class ExternalTemporalState:
    """Per-asset bounded history; uses event timestamps and never fabricates values."""

    def __init__(
        self,
        *,
        max_age: timedelta,
        minimum_points: int = 3,
        deduplicate_source_identities: bool = False,
    ) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        if minimum_points < 2:
            raise ValueError("minimum_points must be at least two")
        self._max_age = max_age
        self._minimum_points = minimum_points
        self._deduplicate_source_identities = deduplicate_source_identities
        self._references: dict[Asset, deque[OfficialReference | ProxyReference]] = {}
        self._books: dict[Asset, deque[CryptoTopOfBook]] = {}
        self._trades: dict[Asset, deque[CryptoTrade]] = {}
        self._dedup_skips: dict[Asset, dict[str, int]] = {}
        self._identity_conflicts: dict[Asset, dict[str, int]] = {}

    def add_reference(self, reference: OfficialReference | ProxyReference) -> None:
        queue = self._references.setdefault(reference.asset, deque())
        if self._deduplicate_source_identities:
            identity = _reference_identity(reference)
            conflict_key = _reference_conflict_key(reference)
            for item in queue:
                if _reference_identity(item) == identity:
                    self._record_dedup_skip(reference.asset, "reference")
                    return
                if _reference_conflict_key(item) == conflict_key:
                    self._record_identity_conflict(reference.asset, "reference")
                    return
        queue.append(reference)

    def add_book(self, book: CryptoTopOfBook) -> None:
        queue = self._books.setdefault(book.asset, deque())
        if self._deduplicate_source_identities:
            identity = _book_identity(book)
            for item in queue:
                if _book_identity(item) != identity:
                    continue
                if _book_payload(item) != _book_payload(book):
                    self._record_identity_conflict(book.asset, "book")
                else:
                    self._record_dedup_skip(book.asset, "book")
                return
        queue.append(book)

    def add_trade(self, trade: CryptoTrade) -> None:
        queue = self._trades.setdefault(trade.asset, deque())
        if self._deduplicate_source_identities:
            identity = _trade_identity(trade)
            if any(_trade_identity(item) == identity for item in queue):
                self._record_dedup_skip(trade.asset, "trade")
                return
        queue.append(trade)

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
            return TemporalFeatureResult(None, "FEATURE_SOURCE_IN_FUTURE", self._diagnostics(asset))
        if observed_at - current_reference.source_ts > self._max_age:
            return TemporalFeatureResult(None, "EXTERNAL_REFERENCE_STALE", self._diagnostics(asset))
        if len(references) < self._minimum_points:
            return TemporalFeatureResult(None, "FEATURE_HISTORY_WARMING", self._diagnostics(asset))
        if not books:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE", self._diagnostics(asset))
        if not trades:
            return TemporalFeatureResult(
                None, "EXTERNAL_TRADE_FLOW_STALE", self._diagnostics(asset)
            )

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
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_FUTURE", self._diagnostics(asset))
        if observed_at - book_source_ts > self._max_age:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE", self._diagnostics(asset))
        spread_mid = (latest_book.bid_price + latest_book.ask_price) / Decimal("2")
        depth_total = latest_book.bid_quantity + latest_book.ask_quantity
        if depth_total <= _ZERO:
            return TemporalFeatureResult(None, "EXTERNAL_BOOK_STALE", self._diagnostics(asset))
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
            return TemporalFeatureResult(
                None, "EXTERNAL_TRADE_FLOW_STALE", self._diagnostics(asset)
            )
        trade_imbalance = signed_volume / total_volume

        signs = [item > _ZERO for item in returns if item != _ZERO]
        flips = sum(1 for left, right in pairwise(signs) if left != right)
        flip_rate = Decimal(flips) / Decimal(max(len(signs) - 1, 1))
        signal_stability = _ONE - min(_ONE, flip_rate)
        regime_score = min(_ONE, realized_volatility * Decimal("100"))
        diagnostics = self._diagnostics(
            asset,
            returns=returns,
            signs=tuple(signs),
            flip_count=flips,
        )
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
            diagnostics,
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

    def _diagnostics(
        self,
        asset: Asset,
        *,
        returns: tuple[Decimal, ...] = (),
        signs: tuple[bool, ...] = (),
        flip_count: int = 0,
    ) -> dict[str, object]:
        references = tuple(self._references.get(asset, ()))
        books = tuple(self._books.get(asset, ()))
        trades = tuple(self._trades.get(asset, ()))
        reference_ts = tuple(item.source_ts for item in references)
        book_ts = tuple(item.lineage.source_ts or item.lineage.recv_ts for item in books)
        trade_ts = tuple(item.lineage.source_ts or item.lineage.recv_ts for item in trades)
        reference_identities = tuple(_reference_identity(item) for item in references)
        book_identities = tuple(_book_identity(item) for item in books)
        trade_identities = tuple(_trade_identity(item) for item in trades)
        dedup_skips = self._dedup_skips.get(asset, {})
        identity_conflicts = self._identity_conflicts.get(asset, {})
        return {
            "scope": "asset_level",
            "asset": asset.value,
            "temporal_semantics": "source_identity_dedup_diagnostic"
            if self._deduplicate_source_identities
            else "legacy_append_all",
            "dedup_policy": "source_identity_v1"
            if self._deduplicate_source_identities
            else "none_legacy_append_all",
            "dedup_cache_scope": "in_memory_ephemeral_per_process",
            "dedup_retention_policy": "bounded_by_temporal_max_age_prune_on_snapshot",
            "max_age_seconds": self._max_age.total_seconds(),
            "minimum_points": self._minimum_points,
            "near_duplicate_interval_seconds": 2,
            "reference_sample_count": len(references),
            "book_sample_count": len(books),
            "trade_sample_count": len(trades),
            "unique_reference_source_timestamp_count": len(set(reference_ts)),
            "unique_book_source_timestamp_count": len(set(book_ts)),
            "unique_trade_source_timestamp_count": len(set(trade_ts)),
            "unique_reference_source_identity_count": len(set(reference_identities)),
            "unique_book_source_identity_count": len(set(book_identities)),
            "unique_trade_source_identity_count": len(set(trade_identities)),
            "duplicate_reference_source_identity_count": (
                len(reference_identities) - len(set(reference_identities))
            ),
            "duplicate_book_source_identity_count": (
                len(book_identities) - len(set(book_identities))
            ),
            "duplicate_trade_source_identity_count": (
                len(trade_identities) - len(set(trade_identities))
            ),
            "dedup_reference_skip_count": int(dedup_skips.get("reference", 0)),
            "dedup_book_skip_count": int(dedup_skips.get("book", 0)),
            "dedup_trade_skip_count": int(dedup_skips.get("trade", 0)),
            "reference_identity_conflict_count": int(
                identity_conflicts.get("reference", 0)
            ),
            "book_identity_conflict_count": int(identity_conflicts.get("book", 0)),
            "near_duplicate_reference_count": _near_duplicate_count(reference_ts),
            "near_duplicate_book_count": _near_duplicate_count(book_ts),
            "near_duplicate_trade_count": _near_duplicate_count(trade_ts),
            "return_sample_count": len(returns),
            "return_count": len(returns),
            "nonzero_return_count": sum(1 for item in returns if item != _ZERO),
            "return_sign_count": len(signs),
            "positive_return_sign_count": sum(1 for item in signs if item),
            "negative_return_sign_count": sum(1 for item in signs if not item),
            "flip_count": flip_count,
            "flip_denominator": max(len(signs) - 1, 1),
        }

    def _record_dedup_skip(self, asset: Asset, kind: str) -> None:
        by_kind = self._dedup_skips.setdefault(asset, {})
        by_kind[kind] = int(by_kind.get(kind, 0)) + 1

    def _record_identity_conflict(self, asset: Asset, kind: str) -> None:
        by_kind = self._identity_conflicts.setdefault(asset, {})
        by_kind[kind] = int(by_kind.get(kind, 0)) + 1


def _return(previous: Decimal, current: Decimal) -> Decimal:
    if previous <= _ZERO:
        raise ValueError("reference history contains non-positive value")
    return (current - previous) / previous


def _mean_abs(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return _ZERO
    return sum((abs(item) for item in values), _ZERO) / Decimal(len(values))


def _near_duplicate_count(timestamps: tuple[datetime, ...]) -> int:
    ordered = tuple(sorted(timestamps))
    return sum(
        1
        for left, right in pairwise(ordered)
        if timedelta(0) <= right - left <= timedelta(seconds=2)
    )


def _reference_identity(
    reference: OfficialReference | ProxyReference,
) -> tuple[str, str, str, str, str]:
    return (
        str(reference.source),
        reference.asset.value,
        reference.source_ts.isoformat(),
        str(reference.value),
        reference.kind.value,
    )


def _reference_conflict_key(
    reference: OfficialReference | ProxyReference,
) -> tuple[str, str, str, str]:
    return (
        str(reference.source),
        reference.asset.value,
        reference.source_ts.isoformat(),
        reference.kind.value,
    )


def _book_identity(book: CryptoTopOfBook) -> tuple[str, str, int]:
    return (book.lineage.source.value, book.asset.value, book.update_id)


def _book_payload(book: CryptoTopOfBook) -> tuple[str, str, str, str]:
    return (
        str(book.bid_price),
        str(book.bid_quantity),
        str(book.ask_price),
        str(book.ask_quantity),
    )


def _trade_identity(trade: CryptoTrade) -> tuple[str, str, int]:
    return (trade.lineage.source.value, trade.asset.value, trade.trade_id)
