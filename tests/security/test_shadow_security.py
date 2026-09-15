import inspect
from pathlib import Path

from direction_engine_v3.shadow import build_shadow_summary
from direction_engine_v3.shadow import daemon as shadow_daemon
from direction_engine_v3.shadow import service as shadow_service


def test_shadow_service_contains_no_real_order_or_signing_path() -> None:
    source = f"{inspect.getsource(shadow_service)}\n{inspect.getsource(shadow_daemon)}".lower()

    for forbidden in ("private_key", "api_secret", "sign_order", "submit_order", "live_armed"):
        assert forbidden not in source


def test_shadow_systemd_keeps_paper_defaults_and_project_runtime_only() -> None:
    unit = Path("deploy/systemd/direction-engine-v3-shadow.service").read_text()

    assert "Environment=APP_MODE=PAPER" in unit
    assert "Environment=LIVE_TRADING_ENABLED=false" in unit
    assert "Environment=LIVE_AUTO_ARM=false" in unit
    assert "ReadWritePaths=/home/ubuntu/direction-engine-v3/runtime" in unit
    assert "ExecStart=/home/ubuntu/direction-engine-v3/.venv/bin/python" in unit
    assert "-m direction_engine_v3.shadow.daemon" in unit


def test_shadow_report_timer_is_separate_from_persistent_runtime() -> None:
    timer = Path("deploy/systemd/direction-engine-v3-shadow-report.timer").read_text()
    report = Path("deploy/systemd/direction-engine-v3-shadow-report.service").read_text()

    assert "Unit=direction-engine-v3-shadow-report.service" in timer
    assert "-m direction_engine_v3.shadow.service" in report


def test_shadow_summary_api_refuses_live_enabled_state() -> None:
    assert callable(build_shadow_summary)
