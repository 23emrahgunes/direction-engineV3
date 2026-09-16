from pathlib import Path

SCRIPT = Path("scripts/v31531_ssm_accept.ps1")


def test_v31531_ssm_bridge_preserves_safe_transport_contracts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Tee-Object" not in source
    assert "[System.Text.UTF8Encoding]::new($false)" in source
    assert "ConvertFrom-Json -InputObject $json" in source
    assert "ConvertTo-LfText -Text $RemoteScript" in source
    assert "#!/usr/bin/env bash" in source
    assert "set -euo pipefail" in source
    assert "/usr/bin/env bash /tmp/v31531_ssm_accept.sh" in source
    assert "direction_engine_v3.diagnostics.rtds_probe" in source
    assert "SHADOW_DAEMON_NOT_ADVANCING" in source
    assert "ssh " not in source.lower()


def test_v31531_ssm_bridge_does_not_enable_live_or_real_orders() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "export LIVE_TRADING_ENABLED=true" not in source
    assert "export LIVE_AUTO_ARM=true" not in source
    assert "real_order_submission': False" in source
    assert "PRIVATE_KEY=" not in source
    assert "API_SECRET=" not in source
    assert "create-order" not in source.lower()
