"""Import-safety tests for the package boundary."""

import os
import subprocess
import sys
from pathlib import Path

TRADING_CREDENTIAL_NAMES = {
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_PRIVATE_KEY",
}


def test_package_import_performs_no_network_io_and_needs_no_credentials() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "src"
    child_environment = {
        key: value for key, value in os.environ.items() if key not in TRADING_CREDENTIAL_NAMES
    }
    existing_pythonpath = child_environment.get("PYTHONPATH")
    child_environment["PYTHONPATH"] = os.pathsep.join(
        path
        for path in (str(source_root), existing_pythonpath)
        if path
    )
    import_script = """
import sys

def block_network(event: str, _args: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise RuntimeError(f"network I/O attempted during import: {event}")

sys.addaudithook(block_network)
import direction_engine_v3
"""

    completed = subprocess.run(
        [sys.executable, "-c", import_script],
        check=False,
        capture_output=True,
        env=child_environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
