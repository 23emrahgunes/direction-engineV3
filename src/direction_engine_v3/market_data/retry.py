"""Bounded reconnect delay policy."""

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    initial_delay_seconds: float = 0.5
    maximum_delay_seconds: float = 15.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_delay_seconds <= 0:
            raise ValueError("initial_delay_seconds must be positive")
        if self.maximum_delay_seconds < self.initial_delay_seconds:
            raise ValueError("maximum_delay_seconds must not be less than initial delay")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay(self, attempt: int, rng: Random) -> float:
        """Return bounded exponential delay for a one-based retry attempt."""

        if attempt < 1 or attempt >= self.max_attempts:
            raise ValueError("attempt must identify a retry before max_attempts")
        base = min(self.maximum_delay_seconds, self.initial_delay_seconds * 2 ** (attempt - 1))
        jitter = base * self.jitter_ratio * rng.uniform(-1, 1)
        return float(max(0.0, min(self.maximum_delay_seconds, base + jitter)))
