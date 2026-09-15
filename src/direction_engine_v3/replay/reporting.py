"""Deterministic replay identity and per-bucket report contracts."""

import hashlib
import json
from dataclasses import dataclass

from direction_engine_v3.domain._validation import require_text, require_tuple, require_unique
from direction_engine_v3.evaluation import EvaluationMetrics
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS, MarketBucket


def canonical_hash(values: tuple[tuple[str, str], ...]) -> str:
    require_tuple("values", values)
    require_unique("configuration keys", (key for key, _ in values))
    encoded = json.dumps(sorted(values), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayManifest:
    dataset_version: str
    code_commit: str
    strategy_version: str
    model_version: str
    configuration: tuple[tuple[str, str], ...]
    deterministic_seed: int
    configuration_hash: str

    def __post_init__(self) -> None:
        for name in ("dataset_version", "code_commit", "strategy_version", "model_version"):
            require_text(name, getattr(self, name))
        if self.deterministic_seed < 0:
            raise ValueError("deterministic_seed must be non-negative")
        if self.configuration_hash != canonical_hash(self.configuration):
            raise ValueError("configuration hash mismatch")


@dataclass(frozen=True, slots=True)
class BucketStrategyReport:
    bucket: MarketBucket
    metrics: EvaluationMetrics
    theoretical_opportunities: int
    executable_opportunities: int
    promotion_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.theoretical_opportunities < self.executable_opportunities:
            raise ValueError("executable opportunities cannot exceed theoretical count")
        require_tuple("promotion_reasons", self.promotion_reasons)


@dataclass(frozen=True, slots=True)
class TwelveBucketReplayReport:
    manifest: ReplayManifest
    buckets: tuple[BucketStrategyReport, ...]

    def __post_init__(self) -> None:
        actual = tuple(item.bucket for item in self.buckets)
        if len(actual) != 12 or set(actual) != set(SUPPORTED_MARKET_BUCKETS):
            raise ValueError("replay report requires all twelve independent buckets")
        if any(not item.promotion_reasons for item in self.buckets):
            raise ValueError("every bucket requires explicit promotion or rejection evidence")
