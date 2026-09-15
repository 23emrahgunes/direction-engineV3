"""Bootstrap configuration acceptance tests."""

import importlib

import pytest

from direction_engine_v3 import (
    APP_MODE,
    LIVE_AUTO_ARM,
    LIVE_TRADING_ENABLED,
    SUPPORTED_ASSETS,
    SUPPORTED_HORIZONS,
    BootstrapSettings,
)

TRADING_CREDENTIAL_NAMES = (
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_PRIVATE_KEY",
)


def test_package_imports_successfully() -> None:
    package = importlib.import_module("direction_engine_v3")

    assert package.__name__ == "direction_engine_v3"


def test_supported_assets_are_exact() -> None:
    assert SUPPORTED_ASSETS == ("BTC", "ETH", "SOL", "XRP")


def test_supported_horizons_are_exact() -> None:
    assert SUPPORTED_HORIZONS == ("5m", "15m", "1h")


def test_app_mode_defaults_to_paper() -> None:
    assert APP_MODE == "PAPER"
    assert BootstrapSettings().app_mode == "PAPER"


def test_live_trading_defaults_to_disabled() -> None:
    assert LIVE_TRADING_ENABLED is False
    assert BootstrapSettings().live_trading_enabled is False


def test_live_auto_arm_defaults_to_disabled() -> None:
    assert LIVE_AUTO_ARM is False
    assert BootstrapSettings().live_auto_arm is False


@pytest.mark.parametrize("credential_name", TRADING_CREDENTIAL_NAMES)
def test_bootstrap_requires_no_trading_credentials(
    monkeypatch: pytest.MonkeyPatch,
    credential_name: str,
) -> None:
    monkeypatch.delenv(credential_name, raising=False)

    assert BootstrapSettings() == BootstrapSettings()
