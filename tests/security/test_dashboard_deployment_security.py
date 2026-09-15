from pathlib import Path


def test_systemd_unit_keeps_paper_and_live_disabled() -> None:
    unit = Path("deploy/systemd/direction-engine-v3-dashboard.service").read_text()

    assert "User=ubuntu" in unit
    assert "WorkingDirectory=/home/ubuntu/direction-engine-v3" in unit
    assert "Environment=APP_MODE=PAPER" in unit
    assert "Environment=LIVE_TRADING_ENABLED=false" in unit
    assert "Environment=LIVE_AUTO_ARM=false" in unit
    assert "ExecStart=/home/ubuntu/direction-engine-v3/.venv/bin/python" in unit
    assert "private_key" not in unit.lower()
    assert "api_secret" not in unit.lower()


def test_nginx_config_binds_localhost_only() -> None:
    config = Path("deploy/nginx/direction-engine-v3-dashboard.conf").read_text()

    assert "listen 127.0.0.1:8131;" in config
    assert "proxy_pass http://127.0.0.1:8130;" in config
    assert "0.0.0.0" not in config


def test_dashboard_static_page_does_not_define_mutation_controls() -> None:
    html = Path("dashboard/web/index.html").read_text().lower()

    for forbidden in ("arm live", "enable live", "submit order", "private_key", "api_secret"):
        assert forbidden not in html
