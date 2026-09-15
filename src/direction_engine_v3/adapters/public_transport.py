"""Credential-free, read-only HTTP and WebSocket transport."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from decimal import Decimal
from random import Random
from typing import Final
from urllib.parse import urlparse

import aiohttp

from direction_engine_v3.market_data.errors import (
    MarketDataSchemaError,
    TransportExhaustedError,
)
from direction_engine_v3.market_data.retry import RetryPolicy

_PUBLIC_HOSTS: Final = frozenset(
    {
        "api.binance.com",
        "clob.polymarket.com",
        "gamma-api.polymarket.com",
        "stream.binance.com",
        "fstream.binance.com",
        "ws-live-data.polymarket.com",
        "ws-subscriptions-clob.polymarket.com",
    }
)


class PublicTransport:
    """Explicit-lifecycle transport that cannot attach credentials or mutate remote state."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "PublicTransport":
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get_json(
        self, url: str, *, params: Mapping[str, str] | None = None
    ) -> object:
        """Perform an allowlisted public GET and return decoded JSON."""

        _validate_public_url(url, scheme="https")
        session = self._require_session()
        async with session.get(url, params=params, allow_redirects=False) as response:
            response.raise_for_status()
            return await response.json(
                content_type=None,
                loads=lambda value: json.loads(value, parse_float=Decimal),
            )

    async def websocket_json(
        self,
        url: str,
        *,
        subscription: Mapping[str, object] | None = None,
        text_heartbeat_seconds: float | None = None,
    ) -> AsyncIterator[object]:
        """Connect to an allowlisted public feed and yield JSON messages."""

        _validate_public_url(url, scheme="wss")
        session = self._require_session()
        async with session.ws_connect(url, heartbeat=20.0, autoclose=True) as websocket:
            if subscription is not None:
                await websocket.send_json(dict(subscription))
            heartbeat = None
            if text_heartbeat_seconds is not None:
                if text_heartbeat_seconds <= 0:
                    raise ValueError("text_heartbeat_seconds must be positive")
                heartbeat = asyncio.create_task(
                    _send_text_heartbeats(websocket, text_heartbeat_seconds)
                )
            try:
                async for message in websocket:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        decoded = _decode_websocket_text(message.data)
                        if decoded is None:
                            if message.data.strip().upper() == "PING":
                                await websocket.send_str("PONG")
                            continue
                        yield decoded
                    elif message.type is aiohttp.WSMsgType.ERROR:
                        raise aiohttp.ClientConnectionError("public WebSocket reported an error")
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat

    async def resilient_websocket_json(
        self,
        url: str,
        *,
        subscription: Mapping[str, object] | None = None,
        text_heartbeat_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> AsyncIterator[object]:
        """Reconnect and resubscribe with bounded exponential backoff."""

        retry_policy = retry_policy or RetryPolicy()
        rng = Random()
        for attempt in range(1, retry_policy.max_attempts + 1):
            try:
                async for item in self.websocket_json(
                    url,
                    subscription=subscription,
                    text_heartbeat_seconds=text_heartbeat_seconds,
                ):
                    yield item
                return
            except (TimeoutError, aiohttp.ClientError) as exc:
                if attempt == retry_policy.max_attempts:
                    raise TransportExhaustedError(
                        f"public WebSocket exhausted {attempt} attempts"
                    ) from exc
                await asyncio.sleep(retry_policy.delay(attempt, rng))

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("PublicTransport must be used as an async context manager")
        return self._session


def _validate_public_url(url: str, *, scheme: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != scheme or parsed.hostname not in _PUBLIC_HOSTS:
        raise MarketDataSchemaError("URL is not an allowlisted public market-data endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise MarketDataSchemaError("credentials are forbidden in public market-data URLs")


async def _send_text_heartbeats(
    websocket: aiohttp.ClientWebSocketResponse, interval_seconds: float
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await websocket.send_str("PING")


def _decode_websocket_text(value: str) -> object | None:
    stripped = value.strip()
    if stripped.upper() in {"PING", "PONG"}:
        return None
    try:
        decoded: object = json.loads(stripped, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise MarketDataSchemaError("public WebSocket emitted a non-JSON data frame") from exc
    return decoded
