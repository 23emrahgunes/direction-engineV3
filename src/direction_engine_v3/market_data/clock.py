"""Explicit wall-clock and monotonic time sources."""

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Protocol


class Clock(Protocol):
    """Clock contract used by transports and freshness checks."""

    def utc_now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock with UTC wall time and monotonic elapsed time."""

    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return monotonic_ns()


def utc_from_milliseconds(value: int | str) -> datetime:
    """Convert Unix milliseconds to an aware UTC timestamp."""

    if isinstance(value, bool):
        raise TypeError("timestamp milliseconds must be an integer")
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp milliseconds must be an integer") from exc
    if milliseconds < 0:
        raise ValueError("timestamp milliseconds must be non-negative")
    return datetime.fromtimestamp(milliseconds / 1_000, tz=UTC)
