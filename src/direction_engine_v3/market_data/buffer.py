"""Bounded in-process event buffers with explicit overflow semantics."""

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum

from direction_engine_v3.market_data.errors import BufferOverflowError


class OverflowPolicy(StrEnum):
    FAIL_CLOSED = "FAIL_CLOSED"
    LATEST_ONLY = "LATEST_ONLY"


@dataclass(slots=True)
class EventBuffer[T]:
    """Bounded queue; callers must explicitly choose lossless failure or latest-only."""

    capacity: int
    overflow_policy: OverflowPolicy
    dropped_count: int = field(default=0, init=False)
    _queue: asyncio.Queue[T] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be positive")
        if not isinstance(self.overflow_policy, OverflowPolicy):
            raise TypeError("overflow_policy must be OverflowPolicy")
        self._queue = asyncio.Queue(maxsize=self.capacity)

    def put_nowait(self, item: T) -> None:
        if not self._queue.full():
            self._queue.put_nowait(item)
            return
        if self.overflow_policy is OverflowPolicy.FAIL_CLOSED:
            raise BufferOverflowError("bounded event buffer overflowed")
        self._queue.get_nowait()
        self._queue.task_done()
        self.dropped_count += 1
        self._queue.put_nowait(item)

    async def get(self) -> T:
        return await self._queue.get()

    @property
    def size(self) -> int:
        return self._queue.qsize()
