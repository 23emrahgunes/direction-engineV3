"""PAPER-only model governance and execution-permission contracts."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from direction_engine_v3.domain import ProbabilityForecast
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket
from direction_engine_v3.models.paper_runtime import (
    PAPER_RESEARCH_BASELINE_MODEL_VERSION,
    PAPER_TRAINED_MODEL_VERSION_PREFIX,
)


class ModelExecutionPermission(StrEnum):
    NONE = "NONE"
    SHADOW = "SHADOW"
    LIMITED_PAPER = "LIMITED_PAPER"
    PAPER = "PAPER"


class ModelGovernanceState(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    CHALLENGER = "CHALLENGER"
    SHADOW_ONLY = "SHADOW_ONLY"
    SHADOW_CANDIDATE = "SHADOW_CANDIDATE"
    SHADOW_PROMOTED = "SHADOW_PROMOTED"
    LIMITED_PAPER = "LIMITED_PAPER"
    PAPER_PROMOTED = "PAPER_PROMOTED"
    HISTORICAL_RESEARCH_ONLY = "HISTORICAL_RESEARCH_ONLY"
    SUSPECT = "SUSPECT"
    NON_PROMOTABLE = "NON_PROMOTABLE"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class ModelGovernanceDecision:
    bucket: MarketBucket
    state: ModelGovernanceState
    execution_permission: ModelExecutionPermission
    reason: str
    model_version: str | None = None
    promotion_id: str | None = None

    @property
    def paper_execution_allowed(self) -> bool:
        return self.execution_permission in {
            ModelExecutionPermission.LIMITED_PAPER,
            ModelExecutionPermission.PAPER,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "asset": self.bucket.asset.value,
            "horizon": self.bucket.horizon.value,
            "model_version": self.model_version,
            "model_governance_state": self.state.value,
            "execution_permission": self.execution_permission.value,
            "promotion_id": self.promotion_id,
            "governance_rejection_reason": self.reason
            if not self.paper_execution_allowed
            else None,
            "real_order_submission": False,
        }


def directional_paper_governance_decision(
    bucket: MarketBucket,
    forecast: ProbabilityForecast | None,
    promoted_models: Mapping[tuple[MarketBucket, str], ModelGovernanceDecision] | None = None,
) -> ModelGovernanceDecision:
    """Return the PAPER execution permission for a Directional model.

    P2.0.0 intentionally grants no PAPER authority. Forecasts may still be produced
    for SHADOW/research evidence, but artifacts do not become trade-authorized merely
    by existing.
    """

    model_version = None if forecast is None else forecast.model_version
    if model_version is not None and promoted_models is not None:
        promoted = promoted_models.get((bucket, model_version))
        if promoted is not None:
            if not promoted.paper_execution_allowed:
                return promoted
            return ModelGovernanceDecision(
                bucket=bucket,
                state=promoted.state,
                execution_permission=promoted.execution_permission,
                reason="PROMOTED_MODEL_AUTHORIZED",
                model_version=model_version,
                promotion_id=promoted.promotion_id,
            )
    if model_version == PAPER_RESEARCH_BASELINE_MODEL_VERSION:
        state = ModelGovernanceState.HISTORICAL_RESEARCH_ONLY
        reason = "MODEL_NOT_PROMOTED"
    elif model_version is not None and model_version.startswith(
        f"{PAPER_TRAINED_MODEL_VERSION_PREFIX}:"
    ):
        state = ModelGovernanceState.SUSPECT
        reason = "MODEL_NOT_PROMOTED"
    elif model_version is None:
        state = ModelGovernanceState.SHADOW_ONLY
        reason = "MODEL_NOT_PROMOTED"
    else:
        state = ModelGovernanceState.NON_PROMOTABLE
        reason = "MODEL_NOT_PROMOTED"
    return ModelGovernanceDecision(
        bucket=bucket,
        state=state,
        execution_permission=ModelExecutionPermission.NONE,
        reason=reason,
        model_version=model_version,
    )


def default_directional_governance_status() -> dict[str, object]:
    return {
        "label": "PAPER / SHADOW — NO REAL ORDER",
        "status": "DIRECTIONAL_MODEL_GOVERNANCE_READY",
        "version": "P2.0.0",
        "real_order_submission": False,
        "buckets": [
            ModelGovernanceDecision(
                bucket,
                ModelGovernanceState.SHADOW_ONLY,
                ModelExecutionPermission.NONE,
                "MODEL_NOT_PROMOTED",
            ).as_dict()
            for bucket in SUPPORTED_MARKET_BUCKETS
        ],
    }
