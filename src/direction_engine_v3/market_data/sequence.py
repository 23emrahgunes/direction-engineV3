"""Sequence continuity tracking for delta streams."""

from dataclasses import dataclass

from direction_engine_v3.market_data.errors import SequenceGapError


@dataclass(slots=True)
class SequenceTracker:
    """Reject deltas until bootstrapped and fail closed on a gap."""

    last_update_id: int | None = None
    resync_required: bool = True

    def restore_snapshot(self, update_id: int) -> None:
        _require_update_id(update_id)
        self.last_update_id = update_id
        self.resync_required = False

    def apply(self, first_update_id: int, final_update_id: int) -> bool:
        """Apply a documented inclusive update range; return False for an old duplicate."""

        _require_update_id(first_update_id)
        _require_update_id(final_update_id)
        if final_update_id < first_update_id:
            raise ValueError("final_update_id cannot precede first_update_id")
        if self.resync_required or self.last_update_id is None:
            raise SequenceGapError("snapshot restoration required before applying deltas")
        expected = self.last_update_id + 1
        if final_update_id < expected:
            return False
        if first_update_id > expected:
            self.resync_required = True
            raise SequenceGapError(f"sequence gap: expected {expected}, received {first_update_id}")
        self.last_update_id = final_update_id
        return True


def _require_update_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("update ID must be an integer")
    if value < 0:
        raise ValueError("update ID must be non-negative")
