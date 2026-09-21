from urllib.error import URLError

from direction_engine_v3.diagnostics import dashboard_tunnel_status as tunnel


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return b"ok"


def test_dashboard_tunnel_status_reports_ready_without_starting_aws(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        assert timeout == 1.0
        return _Response(200)

    monkeypatch.setattr(tunnel, "urlopen", fake_urlopen)

    payload = tunnel.build_status(base_url="http://127.0.0.1:8131/", timeout_seconds=1.0)

    assert payload["status"] == "TUNNEL_READY"
    assert payload["browser_url"] == "http://127.0.0.1:8131/"
    assert "AWS-StartPortForwardingSession" in payload["ssm_port_forward_command"]
    assert "localPortNumber" in payload["ssm_port_forward_command"]
    assert calls == [
        "http://127.0.0.1:8131/",
        "http://127.0.0.1:8131/api/paper/summary",
        "http://127.0.0.1:8131/api/directional/status",
        "http://127.0.0.1:8131/api/shadow/status",
    ]
    assert all(item["ok"] for item in payload["endpoints"])


def test_dashboard_tunnel_status_reports_unavailable_with_sanitized_errors(
    monkeypatch,
) -> None:
    def fake_urlopen(_request, timeout):
        raise URLError("connection refused\nsecret-looking newline removed")

    monkeypatch.setattr(tunnel, "urlopen", fake_urlopen)

    payload = tunnel.build_status(base_url="http://127.0.0.1:8131", timeout_seconds=1.0)

    assert payload["status"] == "TUNNEL_UNAVAILABLE"
    assert all(not item["ok"] for item in payload["endpoints"])
    assert all("\n" not in str(item["error"]) for item in payload["endpoints"])
    encoded = str(payload)
    assert "AWS_ACCESS_KEY_ID" not in encoded
    assert "AWS_SECRET_ACCESS_KEY" not in encoded
    assert "LIVE_TRADING_ENABLED=true" not in encoded
