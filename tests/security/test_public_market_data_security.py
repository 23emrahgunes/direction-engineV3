import asyncio

import pytest

from direction_engine_v3.adapters.public_transport import PublicTransport, _decode_websocket_text
from direction_engine_v3.market_data import MarketDataSchemaError


def test_transport_exposes_read_only_methods_only() -> None:
    public_methods = {name for name in dir(PublicTransport) if not name.startswith("_")}
    assert public_methods == {"get_json", "resilient_websocket_json", "websocket_json"}


def test_transport_rejects_non_allowlisted_and_credential_urls_before_network() -> None:
    transport = PublicTransport()

    async def exercise() -> None:
        with pytest.raises(MarketDataSchemaError):
            await transport.get_json("https://example.com/data")
        with pytest.raises(MarketDataSchemaError, match="credentials"):
            await transport.get_json("https://user:secret@api.binance.com/api/v3/time")

    asyncio.run(exercise())


def test_allowed_url_still_requires_explicit_async_lifecycle() -> None:
    transport = PublicTransport()

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="async context manager"):
            await transport.get_json("https://api.binance.com/api/v3/time")

    asyncio.run(exercise())


@pytest.mark.parametrize("frame", ["", "  ", "PING", "pong", "  Pong\n"])
def test_websocket_control_frames_are_case_insensitive(frame: str) -> None:
    assert _decode_websocket_text(frame) is None


def test_known_connection_frame_is_ignored() -> None:
    assert _decode_websocket_text("connected") is None


def test_unknown_non_json_websocket_frame_fails_closed() -> None:
    with pytest.raises(MarketDataSchemaError, match="non-JSON"):
        _decode_websocket_text("not-json")
