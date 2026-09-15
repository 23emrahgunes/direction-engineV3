"""Secret redaction helpers for diagnostics and dashboard payloads."""

from collections.abc import Mapping

_SENSITIVE_MARKERS = (
    "api_key",
    "api_secret",
    "passphrase",
    "private_key",
    "secret",
    "token",
    "credential",
)
REDACTED = "<redacted>"


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Return a copy with sensitive-looking keys redacted recursively."""

    redacted: dict[str, object] = {}
    for key, value in values.items():
        normalized = key.lower()
        if any(marker in normalized for marker in _SENSITIVE_MARKERS):
            redacted[key] = REDACTED
        elif isinstance(value, Mapping):
            redacted[key] = redact_mapping(
                {str(item_key): item for item_key, item in value.items()}
            )
        elif isinstance(value, tuple):
            redacted[key] = tuple(
                redact_mapping(item) if isinstance(item, Mapping) else item for item in value
            )
        elif isinstance(value, list):
            redacted[key] = [
                redact_mapping(item) if isinstance(item, Mapping) else item for item in value
            ]
        else:
            redacted[key] = value
    return redacted
