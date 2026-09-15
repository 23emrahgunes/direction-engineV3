"""Health and readiness models that fail closed for trading readiness."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from direction_engine_v3.domain._validation import require_text, require_utc


class HealthStatus(StrEnum):
    PASSING = "PASSING"
    DEGRADED = "DEGRADED"
    FAILING = "FAILING"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    status: HealthStatus
    message: str
    checked_at: datetime

    def __post_init__(self) -> None:
        require_text("name", self.name)
        if not isinstance(self.status, HealthStatus):
            raise TypeError("status must be a HealthStatus")
        require_text("message", self.message)
        require_utc("checked_at", self.checked_at)

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    process_alive: ComponentHealth
    database: ComponentHealth
    data_freshness: ComponentHealth
    model_artifacts: ComponentHealth
    trading_readiness: ComponentHealth

    @property
    def ready(self) -> bool:
        return all(
            component.status is HealthStatus.PASSING
            for component in (
                self.process_alive,
                self.database,
                self.data_freshness,
                self.model_artifacts,
                self.trading_readiness,
            )
        )

    def as_dict(self) -> dict[str, object]:
        components = (
            self.process_alive,
            self.database,
            self.data_freshness,
            self.model_artifacts,
            self.trading_readiness,
        )
        return {
            "ready": self.ready,
            "components": [component.as_dict() for component in components],
        }
