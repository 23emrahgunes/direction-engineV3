from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE = REPO_ROOT / "scripts" / "v3151_ssm_accept.ps1"


def _windows_powershell() -> str:
    if sys.platform != "win32":
        pytest.skip("V3.15.1 SSM bridge regression is Windows PowerShell specific")
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")
    return powershell


def test_v3151_ssm_bridge_self_test_prevents_log_json_contamination() -> None:
    powershell = _windows_powershell()

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE),
            "-SelfTest",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    combined = completed.stdout + completed.stderr
    assert "V3.15.1 SSM bridge self-test PASS" in combined
    assert "AWS_ACCESS_KEY" not in combined
    assert "AWS_SECRET" not in combined
    assert "AWS_SESSION_TOKEN" not in combined
    assert "PRIVATE KEY" not in combined


def test_v3151_ssm_bridge_does_not_use_tee_object_for_logging() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "Tee-Object" not in source
    assert 'Invoke-AwsText -AwsArgs @("--version") | Write-Log' not in source


def test_v3151_ssm_bridge_writes_aws_payload_as_utf8_no_bom() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "New-Object System.Text.UTF8Encoding($false)" in source
    assert "[System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)" in source
    assert "ConvertFrom-Json -InputObject $json" in source
    assert 'throw "AWS CLI JSON payload was written with a UTF-8 BOM"' in source
    assert "$payload | ConvertTo-Json -Depth 8 | Set-Content" not in source


def test_v3151_ssm_bridge_executes_remote_script_with_bash_and_lf() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "#!/usr/bin/env bash\nset -euo pipefail" in source
    assert '"/usr/bin/env bash /tmp/v3151_ssm_accept.sh"' in source
    assert "$remoteScriptLf = ConvertTo-LfText -Text $RemoteScript" in source
    assert '$lfText = $Text -replace "`r`n", "`n"' in source
    assert '$lfText = $lfText -replace "`r", "`n"' in source
    assert '"cat > /tmp/v3151_ssm_accept.sh <<' in source
    assert "$RemoteScript," not in source
    assert '"/tmp/v3151_ssm_accept.sh"' not in source
