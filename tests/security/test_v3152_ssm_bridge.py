from pathlib import Path

SCRIPT = Path("scripts/v3152_ssm_accept.ps1")


def test_v3152_ssm_bridge_preserves_safe_transport_contracts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Tee-Object" not in source
    assert "UTF8Encoding]::new($false)" in source
    assert "[System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)" in source
    assert "ConvertFrom-Json" in source
    assert "ReadAllBytes($Path)" in source
    assert "#!/usr/bin/env bash" in source
    assert "set -euo pipefail" in source
    assert "ConvertTo-LfText" in source
    assert "/usr/bin/env bash /tmp/v3152_ssm_accept.sh" in source
    assert "AWS-RunShellScript" in source
    assert "ssh " not in source.lower()


def test_v3152_ssm_bridge_does_not_enable_live_or_log_secret_patterns() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "LIVE_TRADING_ENABLED=true\n" not in source
    assert "LIVE_AUTO_ARM=true\n" not in source
    assert "real_order_submission=true\n" not in source
    assert "export LIVE_TRADING_ENABLED=true" not in source
    assert "export LIVE_AUTO_ARM=true" not in source
    assert "create_order" not in source
    assert "submit_order" not in source
    assert "sign_order" not in source
    assert "AWS_SECRET_ACCESS_KEY" not in source
    assert "PRIVATE KEY" not in source
