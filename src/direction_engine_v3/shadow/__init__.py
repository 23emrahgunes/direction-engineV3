"""Shadow/PAPER evidence collection and promotion-gate reporting."""

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
    "ShadowSummary",
    "StructuralEvidence",
    "build_shadow_summary",
    "write_reports",
]
