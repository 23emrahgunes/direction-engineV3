"""Strict helpers for untrusted public JSON payloads."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from direction_engine_v3.market_data.errors import MarketDataSchemaError

JsonObject = Mapping[str, Any]


def require_object(value: object, name: str = "payload") -> JsonObject:
    if not isinstance(value, Mapping):
        raise MarketDataSchemaError(f"{name} must be an object")
    return value


def require_sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MarketDataSchemaError(f"{name} must be an array")
    return value


def require_str(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise MarketDataSchemaError(f"{key} must be a non-empty string")
    return value


def require_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MarketDataSchemaError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataSchemaError(f"{key} must be an integer") from exc
    if parsed < 0:
        raise MarketDataSchemaError(f"{key} must be non-negative")
    return parsed


def require_bool(payload: JsonObject, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise MarketDataSchemaError(f"{key} must be a bool")
    return value


def require_decimal(payload: JsonObject, key: str) -> Decimal:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise MarketDataSchemaError(f"{key} must be decimal-compatible")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise MarketDataSchemaError(f"{key} must be a decimal") from exc
    if not parsed.is_finite():
        raise MarketDataSchemaError(f"{key} must be finite")
    return parsed


def require_iso8601(payload: JsonObject, key: str) -> datetime:
    value = require_str(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataSchemaError(f"{key} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise MarketDataSchemaError(f"{key} must include a timezone")
    return parsed.astimezone(UTC)
