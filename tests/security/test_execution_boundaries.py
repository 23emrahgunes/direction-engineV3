import inspect

from direction_engine_v3.execution import LiveGateway, PaperGateway


def test_live_gateway_is_interface_only_and_paper_has_no_network_client() -> None:
    assert getattr(LiveGateway, "_is_protocol", False)
    source = inspect.getsource(PaperGateway).lower()
    for forbidden in ("aiohttp", "requests", "socket", "private_key", "api_key"):
        assert forbidden not in source
