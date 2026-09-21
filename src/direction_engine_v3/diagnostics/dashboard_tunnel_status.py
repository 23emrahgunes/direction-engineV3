"""Read-only localhost dashboard tunnel diagnostic.

This module never starts AWS sessions and never mutates runtime state.  It only
checks the operator's local SSM-forwarded dashboard URL and prints the exact
port-forward command that should be kept open in a separate terminal.
"""

import argparse
import json
import time
from dataclasses import dataclass
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL: Final[str] = "http://127.0.0.1:8131"
SSM_PORT_FORWARD_COMMAND: Final[str] = (
    "aws ssm start-session --target i-0c0e730834569177e "
    "--document-name AWS-StartPortForwardingSession "
    "--parameters '{\"portNumber\":[\"8131\"],\"localPortNumber\":[\"8131\"]}' "
    "--profile direction-v3 --region eu-north-1"
)


@dataclass(frozen=True, slots=True)
class EndpointProbe:
    name: str
    url: str
    ok: bool
    status_code: int | None
    elapsed_ms: int
    error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def probe_endpoint(url: str, *, name: str, timeout_seconds: float) -> EndpointProbe:
    started = time.monotonic()
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(response.status)
            response.read(256)
        ok = 200 <= status_code < 400
        error = None if ok else f"HTTP_{status_code}"
    except HTTPError as exc:
        status_code = int(exc.code)
        ok = False
        error = f"HTTP_{exc.code}"
    except (TimeoutError, URLError, OSError) as exc:
        status_code = None
        ok = False
        error = _sanitize_error(exc)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return EndpointProbe(name, url, ok, status_code, elapsed_ms, error)


def build_status(
    *, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 3.0
) -> dict[str, object]:
    base = base_url.rstrip("/")
    endpoints = (
        ("root", f"{base}/"),
        ("paper_summary", f"{base}/api/paper/summary"),
        ("directional_status", f"{base}/api/directional/status"),
        ("shadow_status", f"{base}/api/shadow/status"),
    )
    probes = tuple(
        probe_endpoint(url, name=name, timeout_seconds=timeout_seconds)
        for name, url in endpoints
    )
    if all(item.ok for item in probes):
        status = "TUNNEL_READY"
    elif any(item.ok for item in probes):
        status = "TUNNEL_PARTIAL"
    else:
        status = "TUNNEL_UNAVAILABLE"
    return {
        "status": status,
        "base_url": base,
        "browser_url": f"{base}/",
        "ssm_port_forward_command": SSM_PORT_FORWARD_COMMAND,
        "endpoints": [item.as_dict() for item in probes],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local dashboard SSM tunnel status.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    payload = build_status(base_url=args.base_url, timeout_seconds=args.timeout_seconds)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"status: {payload['status']}")
    print(f"browser_url: {payload['browser_url']}")
    print(f"ssm_port_forward_command: {payload['ssm_port_forward_command']}")
    endpoints = cast(list[dict[str, object]], payload["endpoints"])
    for endpoint in endpoints:
        print(
            "{name}: ok={ok} status={status_code} elapsed_ms={elapsed_ms} error={error}".format(
                **endpoint
            )
        )


def _sanitize_error(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").replace("\r", " ")
    if not message:
        message = type(exc).__name__
    return f"{type(exc).__name__}:{message[:240]}"


if __name__ == "__main__":
    main()
