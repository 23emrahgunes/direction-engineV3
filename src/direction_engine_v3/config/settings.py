"""Immutable V3.0 bootstrap defaults.

Environment loading and credential handling are intentionally outside this bootstrap.
"""

from dataclasses import dataclass
from typing import Final

from direction_engine_v3.domain import Asset, Horizon, TradingMode

SUPPORTED_ASSETS: Final[tuple[str, ...]] = tuple(asset.value for asset in Asset)
SUPPORTED_HORIZONS: Final[tuple[str, ...]] = tuple(horizon.value for horizon in Horizon)
APP_MODE: Final[str] = TradingMode.PAPER.value
LIVE_TRADING_ENABLED: Final[bool] = False
LIVE_AUTO_ARM: Final[bool] = False


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    """Import-safe settings available during repository bootstrap."""

    supported_assets: tuple[str, ...] = SUPPORTED_ASSETS
    supported_horizons: tuple[str, ...] = SUPPORTED_HORIZONS
    app_mode: str = APP_MODE
    live_trading_enabled: bool = LIVE_TRADING_ENABLED
    live_auto_arm: bool = LIVE_AUTO_ARM
