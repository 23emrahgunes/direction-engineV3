"""Sanitized Chainlink RTDS fanout probe for V3.15.3.4.1 acceptance.

Network I/O happens only from ``main``/``probe_chainlink_fanout``.
"""

import argparse
import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from direction_engine_v3.adapters.chainlink import (
    RTDS_HEARTBEAT_SECONDS,
    RTDS_WS_URL,
    inspect_twap_frame,
    parse_twap_from_symbol,
    twap_subscription,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data.clock import SystemClock
from direction_engine_v3.market_data.errors import MarketDataSchemaError
from direction_engine_v3.market_data.retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class FanoutProbeConfig:
    duration_seconds: float = 10.0
    frames_per_asset: int = 5


async def probe_chainlink_fanout(config: FanoutProbeConfig) -> dict[str, object]:
    """Open production-style per-asset subscriptions and return sanitized evidence."""

    started_at = datetime.now(UTC)
    async with PublicTransport(timeout_seconds=config.duration_seconds + 10.0) as transport:
        results = await asyncio.gather(
            *(_probe_asset(transport, asset, config) for asset in Asset)
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "url": RTDS_WS_URL,
        "topic": "crypto_prices_twap_sixty",
        "real_order_submission": False,
        "results": results,
    }


async def _probe_asset(
    transport: PublicTransport,
    asset: Asset,
    config: FanoutProbeConfig,
) -> dict[str, object]:
    clock = SystemClock()
    evidence: dict[str, Any] = {
        "intended_asset": asset.value,
        "subscription": _sanitize_subscription(twap_subscription(asset, window_seconds=60)),
        "websocket_status": "NOT_STARTED",
        "subscription_status": "NOT_SENT",
        "frames": [],
        "error": None,
    }
    deadline = asyncio.get_running_loop().time() + config.duration_seconds
    try:
        evidence["websocket_status"] = "CONNECTING"
        async for raw in transport.resilient_websocket_json(
            RTDS_WS_URL,
            subscription=twap_subscription(asset, window_seconds=60),
            text_heartbeat_seconds=RTDS_HEARTBEAT_SECONDS,
            retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.25),
        ):
            evidence["websocket_status"] = "CONNECTED"
            evidence["subscription_status"] = "SUBSCRIBED"
            for payload in _iter_payloads(raw):
                evidence["frames"].append(
                    _sanitize_frame(payload, intended_asset=asset, clock=clock)
                )
                if len(evidence["frames"]) >= config.frames_per_asset:
                    return evidence
            if asyncio.get_running_loop().time() >= deadline:
                return evidence
    except Exception as exc:
        evidence["error"] = _safe_error(exc)
        if evidence["websocket_status"] == "NOT_STARTED":
            evidence["websocket_status"] = "ERROR"
    return evidence


def _sanitize_frame(
    payload: object,
    *,
    intended_asset: Asset,
    clock: SystemClock,
) -> dict[str, object]:
    topic = None
    message_type = None
    returned_symbol = None
    payload_keys: tuple[str, ...] = ()
    data_item_count = None
    data_item_keys: tuple[str, ...] = ()
    window_s = None
    parse_error = None
    returned_asset = None
    frame_class = None
    filter_status = "FILTER_UNKNOWN"
    if isinstance(payload, Mapping):
        topic = payload.get("topic")
        message_type = payload.get("type")
        envelope = payload.get("payload")
        if isinstance(envelope, Mapping):
            returned_symbol = envelope.get("symbol")
            payload_keys = _sorted_keys(envelope)
            window_s = envelope.get("window_s")
            raw_data = envelope.get("data")
            if isinstance(raw_data, Mapping):
                data_item_count = 1
                data_item_keys = _sorted_keys(raw_data)
            elif isinstance(raw_data, list):
                data_item_count = len(raw_data)
                if len(raw_data) == 1 and isinstance(raw_data[0], Mapping):
                    data_item_keys = _sorted_keys(raw_data[0])
    try:
        frame = inspect_twap_frame(payload, window_seconds=60)
        frame_class = frame.frame_class
        returned_asset = frame.asset.value
        filter_status = (
            "FILTER_MATCH" if frame.asset is intended_asset else "FILTER_MISMATCH"
        )
        if frame.frame_class == "LIVE_UPDATE":
            parse_twap_from_symbol(
                payload,
                window_seconds=60,
                recv_ts=clock.utc_now(),
                normalized_ts=clock.utc_now(),
                recv_monotonic_ns=clock.monotonic_ns(),
            )
    except MarketDataSchemaError as exc:
        parse_error = _safe_error(exc)
    return {
        "intended_asset": intended_asset.value,
        "returned_topic": topic,
        "returned_type": message_type,
        "returned_symbol": returned_symbol,
        "returned_asset": returned_asset,
        "frame_class": frame_class,
        "payload_keys": list(payload_keys),
        "data_item_count": data_item_count,
        "data_item_keys": list(data_item_keys),
        "window_s": window_s,
        "parse_error": parse_error,
        "filter_status": filter_status,
    }


def _sanitize_subscription(subscription: Mapping[str, object]) -> dict[str, object]:
    items = subscription.get("subscriptions")
    if not isinstance(items, list) or not items:
        return {"shape": "unexpected"}
    item = items[0]
    if not isinstance(item, Mapping):
        return {"shape": "unexpected"}
    return {
        "topic": item.get("topic"),
        "type": item.get("type"),
        "filters": item.get("filters"),
    }


def _iter_payloads(raw: object) -> tuple[object, ...]:
    if isinstance(raw, list):
        return tuple(raw)
    return (raw,)


def _sorted_keys(value: Mapping[object, object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(key)
            for key in value
            if "secret" not in str(key).lower()
            and "token" not in str(key).lower()
            and "auth" not in str(key).lower()
            and "key" not in str(key).lower()
        )
    )


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    for marker in ("secret", "token", "authorization", "api_key", "private"):
        if marker in text.lower():
            return f"{type(exc).__name__}: <redacted>"
    return f"{type(exc).__name__}: {text}"[:500]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--frames-per-asset", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        probe_chainlink_fanout(
            FanoutProbeConfig(
                duration_seconds=args.duration_seconds,
                frames_per_asset=args.frames_per_asset,
            )
        )
    )
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
