"""Credential-free RTDS source probe used by V3.15.3.1 acceptance.

The module is import-safe: network I/O happens only from ``main``/``probe_rtds``.
"""

import argparse
import asyncio
import json
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import aiohttp

from direction_engine_v3.adapters.chainlink.rtds import RTDS_WS_URL, parse_twap
from direction_engine_v3.domain import Asset
from direction_engine_v3.market_data.clock import SystemClock
from direction_engine_v3.market_data.errors import MarketDataSchemaError

_TOPICS: Final = ("crypto_prices_chainlink", "crypto_prices_twap_sixty")
_HOST: Final = "ws-live-data.polymarket.com"


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    topic: str
    asset: Asset

    @property
    def symbol(self) -> str:
        return f"{self.asset.value.lower()}/usd"


async def probe_rtds(
    *,
    duration_seconds: float = 30.0,
    topics: tuple[str, ...] = _TOPICS,
) -> dict[str, object]:
    """Probe RTDS topics independently and return sanitized evidence."""

    started_at = datetime.now(UTC)
    results: list[dict[str, object]] = []
    dns = _resolve_dns(_HOST)
    tls = await _probe_tls(_HOST)
    dns_status = str(dns["status"])
    tls_status = str(tls["status"])
    for topic in topics:
        for asset in Asset:
            results.append(
                await _probe_target(
                    ProbeTarget(topic, asset),
                    duration_seconds=duration_seconds,
                    dns_status=dns_status,
                    tls_status=tls_status,
                )
            )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "url": RTDS_WS_URL,
        "dns": dns,
        "tls": tls,
        "topics": list(topics),
        "results": results,
        "real_order_submission": False,
    }


def subscription_for(topic: str, asset: Asset) -> dict[str, object]:
    """Build a public RTDS subscription for one topic/asset probe target."""

    if topic not in _TOPICS:
        raise ValueError("unsupported RTDS probe topic")
    return {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": topic,
                "type": "update",
                "filters": f'{{"symbol":"{asset.value.lower()}/usd"}}',
            }
        ],
    }


def sanitize_message_shape(value: object, *, depth: int = 0) -> object:
    """Return key/type shape without leaking raw credentials or headers."""

    if depth > 4:
        return type(value).__name__
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_message_shape(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if "secret" not in str(key).lower()
            and "key" not in str(key).lower()
            and "token" not in str(key).lower()
            and "auth" not in str(key).lower()
        }
    if isinstance(value, list):
        return [sanitize_message_shape(value[0], depth=depth + 1)] if value else []
    if isinstance(value, str):
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, Decimal):
        return "decimal"
    if value is None:
        return None
    return type(value).__name__


async def _probe_target(
    target: ProbeTarget,
    *,
    duration_seconds: float,
    dns_status: str,
    tls_status: str,
) -> dict[str, object]:
    clock = SystemClock()
    subscription = subscription_for(target.topic, target.asset)
    evidence: dict[str, Any] = {
        "topic": target.topic,
        "asset": target.asset.value,
        "symbol": target.symbol,
        "dns_status": dns_status,
        "tcp_tls_status": tls_status,
        "websocket_status": "NOT_STARTED",
        "subscription_status": "NOT_SENT",
        "message_count": 0,
        "parse_success_count": 0,
        "parse_failure_count": 0,
        "first_message_shape": None,
        "topic_returned": None,
        "symbol_returned": None,
        "first_source_timestamp": None,
        "latest_source_timestamp": None,
        "first_value": None,
        "latest_value": None,
        "error": None,
    }
    timeout = aiohttp.ClientTimeout(total=duration_seconds + 10.0)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.ws_connect(RTDS_WS_URL, heartbeat=20.0, autoclose=True) as ws,
        ):
            evidence["websocket_status"] = "CONNECTED"
            await ws.send_json(subscription)
            evidence["subscription_status"] = "SENT"
            deadline = asyncio.get_running_loop().time() + duration_seconds
            while asyncio.get_running_loop().time() < deadline:
                try:
                    message = await ws.receive(timeout=1.0)
                except TimeoutError:
                    continue
                if message.type is aiohttp.WSMsgType.TEXT:
                    if message.data.strip().upper() == "PING":
                        await ws.send_str("PONG")
                        continue
                    try:
                        decoded = json.loads(message.data, parse_float=Decimal)
                    except json.JSONDecodeError as exc:
                        evidence["error"] = _safe_error(exc)
                        continue
                    _record_probe_message(evidence, decoded)
                    _try_parse_supported(evidence, decoded, target, clock)
                elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break
    except Exception as exc:
        evidence["error"] = _safe_error(exc)
        if evidence["websocket_status"] == "NOT_STARTED":
            evidence["websocket_status"] = "ERROR"
    return evidence


def _record_probe_message(evidence: dict[str, Any], decoded: object) -> None:
    payload = _first_payload(decoded)
    evidence["message_count"] = int(evidence["message_count"]) + 1
    if evidence["first_message_shape"] is None:
        evidence["first_message_shape"] = sanitize_message_shape(payload)
    if isinstance(payload, Mapping):
        evidence["topic_returned"] = payload.get("topic")
        inner = payload.get("payload")
        if isinstance(inner, Mapping):
            evidence["symbol_returned"] = inner.get("symbol")
            source_ts = inner.get("timestamp")
            value = inner.get("full_accuracy_value") or inner.get("value")
            if source_ts is not None:
                if evidence["first_source_timestamp"] is None:
                    evidence["first_source_timestamp"] = str(source_ts)
                evidence["latest_source_timestamp"] = str(source_ts)
            if value is not None:
                if evidence["first_value"] is None:
                    evidence["first_value"] = str(value)
                evidence["latest_value"] = str(value)


def _try_parse_supported(
    evidence: dict[str, Any],
    decoded: object,
    target: ProbeTarget,
    clock: SystemClock,
) -> None:
    if target.topic != "crypto_prices_twap_sixty":
        return
    try:
        parse_twap(
            _first_payload(decoded),
            asset=target.asset,
            window_seconds=60,
            recv_ts=clock.utc_now(),
            normalized_ts=clock.utc_now(),
            recv_monotonic_ns=clock.monotonic_ns(),
        )
        evidence["parse_success_count"] = int(evidence["parse_success_count"]) + 1
    except MarketDataSchemaError:
        evidence["parse_failure_count"] = int(evidence["parse_failure_count"]) + 1


def _first_payload(decoded: object) -> object:
    if isinstance(decoded, list) and decoded:
        return decoded[0]
    return decoded


def _resolve_dns(host: str) -> dict[str, object]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443)})
        return {"status": "OK", "addresses": addresses}
    except OSError as exc:
        return {"status": "ERROR", "error": _safe_error(exc)}


async def _probe_tls(host: str) -> dict[str, object]:
    try:
        reader, writer = await asyncio.open_connection(
            host, 443, ssl=ssl.create_default_context(), server_hostname=host
        )
        writer.close()
        await writer.wait_closed()
        del reader
        return {"status": "OK"}
    except Exception as exc:
        return {"status": "ERROR", "error": _safe_error(exc)}


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    for marker in ("secret", "token", "authorization", "api_key", "private"):
        if marker in text.lower():
            return f"{type(exc).__name__}: <redacted>"
    return f"{type(exc).__name__}: {text}"[:500]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(probe_rtds(duration_seconds=args.duration_seconds))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
