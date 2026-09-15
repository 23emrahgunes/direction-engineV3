"""Internal validation helpers for immutable domain snapshots."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal


def require_text(name: str, value: str) -> None:
    """Require a non-empty, already-trimmed string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")


def require_utc(name: str, value: datetime) -> None:
    """Require a timezone-aware UTC datetime."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")


def require_time_order(
    earlier_name: str,
    earlier: datetime,
    later_name: str,
    later: datetime,
    *,
    allow_equal: bool = True,
) -> None:
    """Validate two UTC timestamps and their ordering."""

    require_utc(earlier_name, earlier)
    require_utc(later_name, later)
    valid = earlier <= later if allow_equal else earlier < later
    if not valid:
        operator = "<=" if allow_equal else "<"
        raise ValueError(f"{earlier_name} must be {operator} {later_name}")


def require_decimal(
    name: str,
    value: Decimal,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    minimum_exclusive: bool = False,
) -> None:
    """Require a finite Decimal within optional bounds."""

    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if minimum is not None:
        below_minimum = value <= minimum if minimum_exclusive else value < minimum
        if below_minimum:
            qualifier = "greater than" if minimum_exclusive else "at least"
            raise ValueError(f"{name} must be {qualifier} {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")


def require_tuple[T](name: str, value: tuple[T, ...]) -> None:
    """Require tuple storage so snapshots cannot retain mutable sequences."""

    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")


def require_unique(name: str, values: Iterable[object]) -> None:
    """Require hashable values to be unique."""

    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"{name} must be unique")
