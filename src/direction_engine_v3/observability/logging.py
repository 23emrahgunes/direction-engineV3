"""Import-safe structured logging event builders."""

from dataclasses import dataclass
from datetime import datetime

from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.observability.redaction import redact_mapping


@dataclass(frozen=True, slots=True)
class StructuredLogEvent:
    event_type: str
    severity: str
    occurred_at: datetime
    fields: dict[str, object]

    def __post_init__(self) -> None:
        require_text("event_type", self.event_type)
        require_text("severity", self.severity)
        require_utc("occurred_at", self.occurred_at)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "occurred_at": self.occurred_at.isoformat(),
            "fields": redact_mapping(self.fields),
        }


def build_log_event(
    event_type: str,
    *,
    severity: str,
    occurred_at: datetime,
    fields: dict[str, object],
) -> StructuredLogEvent:
    return StructuredLogEvent(event_type, severity, occurred_at, redact_mapping(fields))
