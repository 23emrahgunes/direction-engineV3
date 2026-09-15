"""Frozen dependency-free logistic probability artifact."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from direction_engine_v3.domain import FeatureVector
from direction_engine_v3.domain._validation import require_decimal, require_text

_ZERO = Decimal("0")
_ONE = Decimal("1")


def feature_schema_hash(feature_names: tuple[str, ...]) -> str:
    if not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError("feature_names must be non-empty and unique")
    for name in feature_names:
        require_text("feature_name", name)
        lowered = name.lower()
        if any(term in lowered for term in ("polymarket", "clob", "contract_price")):
            raise ValueError("predictive feature schema cannot contain Polymarket price data")
    encoded = json.dumps(feature_names, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LogisticArtifact:
    model_version: str
    feature_set_version: str
    feature_names: tuple[str, ...]
    coefficients: tuple[Decimal, ...]
    intercept: Decimal
    schema_hash: str

    def __post_init__(self) -> None:
        require_text("model_version", self.model_version)
        require_text("feature_set_version", self.feature_set_version)
        expected_hash = feature_schema_hash(self.feature_names)
        if self.schema_hash != expected_hash:
            raise ValueError("schema_hash does not match feature_names")
        if len(self.coefficients) != len(self.feature_names):
            raise ValueError("coefficients must match feature_names")
        for coefficient in self.coefficients:
            require_decimal("coefficient", coefficient)
        require_decimal("intercept", self.intercept)

    def predict_raw(self, features: FeatureVector) -> Decimal:
        if features.feature_set_version != self.feature_set_version:
            raise ValueError("feature set version mismatch")
        values = {feature.name: feature.value for feature in features.features}
        if set(values) != set(self.feature_names):
            raise ValueError("feature vector does not exactly match frozen schema")
        score = self.intercept + sum(
            (coefficient * values[name] for name, coefficient in zip(
                self.feature_names, self.coefficients, strict=True
            )),
            _ZERO,
        )
        if score >= _ZERO:
            inverse = (-score).exp()
            return _ONE / (_ONE + inverse)
        exponential = score.exp()
        return exponential / (_ONE + exponential)
