"""Shadow/PAPER evidence collection and promotion-gate reporting."""

from direction_engine_v3.shadow.daemon import (
    ShadowCycleResult,
    ShadowDaemon,
    ShadowMarketState,
    new_evidence_window,
)
from direction_engine_v3.shadow.evidence import (
    AWSIdentityEvidence,
    BucketEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
    PromotionState,
    SettlementStatus,
    ShadowSummary,
    StructuralEvidence,
)
from direction_engine_v3.shadow.reporting import build_shadow_summary, write_reports
from direction_engine_v3.shadow.storage import SQLiteShadowRepository

__all__ = [
    "AWSIdentityEvidence",
    "BucketEvidence",
    "EvidenceFingerprint",
    "EvidenceWindow",
    "PromotionState",
    "SQLiteShadowRepository",
    "SettlementStatus",
    "ShadowCycleResult",
    "ShadowDaemon",
    "ShadowMarketState",
    "ShadowSummary",
    "StructuralEvidence",
    "build_shadow_summary",
    "new_evidence_window",
    "write_reports",
]
