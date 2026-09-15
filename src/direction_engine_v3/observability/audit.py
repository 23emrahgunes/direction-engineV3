"""Audit event helpers for observable but secret-free operations."""

from dataclasses import dataclass
from datetime import datetime

from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.observability.redaction import redact_mapping


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    actor: str
    action: str
    reference_id: str
    occurred_at: datetime
    metadata: dict[str, object]

    def __post_init__(self) -> None:
        require_text("event_id", self.event_id)
        require_text("actor", self.actor)
        require_text("action", self.action)
        require_text("reference_id", self.reference_id)
        require_utc("occurred_at", self.occurred_at)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "actor": self.actor,
            "action": self.action,
            "reference_id": self.reference_id,
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": redact_mapping(self.metadata),
        }
