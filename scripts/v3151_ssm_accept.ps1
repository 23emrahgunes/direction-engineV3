<#
V3.15.1 user-context SSM acceptance bridge.

Run this script from the normal Windows PowerShell user context where the
`direction-v3` AWS profile is already authenticated. The script never reads or
prints AWS config/credential file contents, never copies credentials, and uses
only AWS Systems Manager Run Command for VPS automation.
#>

[CmdletBinding()]
param(
    [string]$Profile = "direction-v3",
    [string]$Region = "eu-north-1",
    [string]$InstanceId = "i-0c0e730834569177e",
    [string]$ProjectDir = "/home/ubuntu/direction-engine-v3",
    [string]$ExpectedMinimumCommit = "7762a0c5f239f54ca44db7b7c4e31ecfb0111dda",
    [int]$PollSeconds = 5,
    [int]$CommandTimeoutSeconds = 1800,
    [switch]$SelfTest,
    [string]$AwsExecutable = "aws"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($SelfTest) {
    $AcceptanceDir = Join-Path ([System.IO.Path]::GetTempPath()) ("v3151_ssm_accept_selftest_" + [Guid]::NewGuid().ToString("N"))
} else {
    $AcceptanceDir = Join-Path $RepoRoot "runtime\acceptance"
}
$JsonPath = Join-Path $AcceptanceDir "v3151_ssm_acceptance.json"
$LogPath = Join-Path $AcceptanceDir "v3151_ssm_acceptance.log"
New-Item -ItemType Directory -Force -Path $AcceptanceDir | Out-Null
$script:AwsExecutable = $AwsExecutable

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f ([DateTimeOffset]::UtcNow.ToString("o")), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Write-LogBlock {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return
    }
    Add-Content -LiteralPath $LogPath -Value $Text -Encoding UTF8
    Write-Host $Text
}

function Save-Result {
    param([hashtable]$Result)
    $Result.generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    $Result | ConvertTo-Json -Depth 12 | Set-Content -Path $JsonPath -Encoding UTF8
}

function Write-AwsCliJsonPayload {
    param(
        [hashtable]$Payload,
        [string]$Path
    )
    $json = $Payload | ConvertTo-Json -Depth 8
    [void](ConvertFrom-Json -InputObject $json)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)

    [byte[]]$firstBytes = [System.IO.File]::ReadAllBytes($Path) | Select-Object -First 3
    if (
        $firstBytes.Length -ge 3 -and
        $firstBytes[0] -eq 0xEF -and
        $firstBytes[1] -eq 0xBB -and
        $firstBytes[2] -eq 0xBF
    ) {
        throw "AWS CLI JSON payload was written with a UTF-8 BOM"
    }
    [void](ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Path, $utf8NoBom)))
}

function ConvertTo-LfText {
    param([string]$Text)
    $lfText = $Text -replace "`r`n", "`n"
    $lfText = $lfText -replace "`r", "`n"
    return $lfText
}

function ConvertTo-ProcessArgument {
    param([string]$Argument)
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    return '"' + ($Argument -replace '\\', '\\' -replace '"', '\"') + '"'
}

function Invoke-AwsText {
    param([string[]]$AwsArgs)
    Write-Log ("aws " + ($AwsArgs -join " "))
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $processInfo = New-Object System.Diagnostics.ProcessStartInfo
        $processInfo.FileName = $script:AwsExecutable
        $processInfo.Arguments = (($AwsArgs | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
        $processInfo.UseShellExecute = $false
        $processInfo.RedirectStandardOutput = $true
        $processInfo.RedirectStandardError = $true
        $processInfo.CreateNoWindow = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $processInfo
        [void]$process.Start()
        $process.StandardOutput.ReadToEnd() | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
        $process.StandardError.ReadToEnd() | Set-Content -LiteralPath $stderrPath -Encoding UTF8
        $process.WaitForExit()
        $exit = $process.ExitCode
        $stdoutRaw = Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
        $stderrRaw = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        if ($null -eq $stdoutRaw) {
            $stdout = ""
        } else {
            $stdout = ([string]$stdoutRaw).Trim()
        }
        if ($null -eq $stderrRaw) {
            $stderr = ""
        } else {
            $stderr = ([string]$stderrRaw).Trim()
        }
        if ($exit -ne 0) {
            $errorText = (($stdout, $stderr) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join "`n"
            Write-LogBlock $errorText
            throw "AWS command failed ($exit): aws $($AwsArgs -join ' ')`n$errorText"
        }
        return $stdout
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-AwsJson {
    param([string[]]$AwsArgs)
    $text = Invoke-AwsText -AwsArgs $AwsArgs
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw "AWS command returned empty JSON: aws $($AwsArgs -join ' ')"
    }
    return ConvertFrom-Json -InputObject $text
}

function Assert-SelfTest {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw "Self-test failed: $Message"
    }
}

function Invoke-BridgeSelfTest {
    $fakeAwsPath = Join-Path $AcceptanceDir "fake-aws.cmd"
    @'
@echo off
if "%1"=="--version" (
  echo aws-cli/2.selftest Python/3.selftest Windows/selftest
  exit /b 0
)
if "%1"=="json" (
  echo {"UserId":"selftest-user","Account":"123456789012","Arn":"arn:aws:iam::123456789012:root"}
  exit /b 0
)
if "%1"=="fail" (
  echo selftest stderr line 1>&2
  exit /b 7
)
echo unknown fake aws args 1>&2
exit /b 3
'@ | Set-Content -LiteralPath $fakeAwsPath -Encoding ASCII

    $script:AwsExecutable = $fakeAwsPath

    $captured = @(Write-Log "self-test log line")
    Assert-SelfTest ($captured.Count -eq 0) "Write-Log emitted success-pipeline output"
    $logText = Get-Content -LiteralPath $LogPath -Raw
    Assert-SelfTest ($logText -match "\[\d{4}-\d{2}-\d{2}T") "Write-Log did not write a timestamped log line"
    Assert-SelfTest ($logText -match "self-test log line") "Write-Log did not write the message to disk"

    $jsonText = Invoke-AwsText -AwsArgs @("json")
    Assert-SelfTest ($jsonText.Trim().StartsWith("{")) "Invoke-AwsText did not return raw JSON"
    Assert-SelfTest ($jsonText -notmatch "^\[\d{4}-\d{2}-\d{2}T") "Invoke-AwsText returned a timestamped log line"
    $parsedText = ConvertFrom-Json -InputObject $jsonText
    Assert-SelfTest ($parsedText.UserId -eq "selftest-user") "Mocked JSON stdout was contaminated before ConvertFrom-Json"

    $parsed = Invoke-AwsJson -AwsArgs @("json")
    Assert-SelfTest ($parsed.Account -eq "123456789012") "Invoke-AwsJson did not parse mocked AWS JSON"

    $failed = $false
    try {
        Invoke-AwsText -AwsArgs @("fail") | Out-Null
    } catch {
        $failed = $true
        Assert-SelfTest ([string]$_ -match "selftest stderr line") "AWS stderr was not visible in the thrown error"
    }
    Assert-SelfTest $failed "Failing AWS command did not fail"
    $logText = Get-Content -LiteralPath $LogPath -Raw
    Assert-SelfTest ($logText -match "selftest stderr line") "AWS stderr was not recorded in the log"
    Assert-SelfTest ($logText -notmatch "AWS_ACCESS_KEY|AWS_SECRET|AWS_SESSION_TOKEN|PRIVATE KEY|BEGIN .*KEY|api_secret|passphrase") "Sensitive credential pattern was logged"

    $payloadPath = Join-Path $AcceptanceDir "payload.json"
    Write-AwsCliJsonPayload -Payload @{
        DocumentName = "AWS-RunShellScript"
        InstanceIds = @("i-selftest")
        Parameters = @{ commands = @("echo selftest") }
    } -Path $payloadPath
    $payloadBytes = [System.IO.File]::ReadAllBytes($payloadPath)
    Assert-SelfTest ($payloadBytes.Length -gt 3) "Self-test payload was not written"
    $hasBom = (
        $payloadBytes[0] -eq 0xEF -and
        $payloadBytes[1] -eq 0xBB -and
        $payloadBytes[2] -eq 0xBF
    )
    Assert-SelfTest (-not $hasBom) "AWS CLI payload contained a UTF-8 BOM"
    $payloadJson = [System.IO.File]::ReadAllText($payloadPath, (New-Object System.Text.UTF8Encoding($false)))
    $payloadParsed = ConvertFrom-Json -InputObject $payloadJson
    Assert-SelfTest ($payloadParsed.DocumentName -eq "AWS-RunShellScript") "AWS CLI payload JSON did not parse after NO BOM write"

    $remoteScript = New-RemoteAcceptanceScript
    $remoteLines = $remoteScript -split "`n"
    Assert-SelfTest ($remoteLines[0] -eq "#!/usr/bin/env bash") "Remote acceptance script first line is not a Bash shebang"
    Assert-SelfTest ($remoteScript -match "set -euo pipefail") "Remote acceptance script lost fail-closed Bash pipefail behavior"

    $crlfText = "one`r`ntwo`rthree`n"
    $lfText = ConvertTo-LfText -Text $crlfText
    Assert-SelfTest ($lfText -notmatch "`r") "LF normalization left CR bytes in text"
    Assert-SelfTest ($lfText -eq "one`ntwo`nthree`n") "LF normalization did not preserve expected line structure"

    $remoteScriptLf = ConvertTo-LfText -Text $remoteScript
    Assert-SelfTest ($remoteScriptLf -notmatch "`r") "Remote acceptance script retained CR bytes after normalization"
    $ssmCommands = @(
        "cat > /tmp/v3151_ssm_accept.sh <<'V3151_BASH'",
        $remoteScriptLf,
        "V3151_BASH",
        "chmod 700 /tmp/v3151_ssm_accept.sh",
        "/usr/bin/env bash /tmp/v3151_ssm_accept.sh"
    )
    Assert-SelfTest ($ssmCommands[-1] -eq "/usr/bin/env bash /tmp/v3151_ssm_accept.sh") "SSM command does not explicitly invoke Bash"
    Assert-SelfTest (($ssmCommands -join "`n") -notmatch "`r") "SSM command payload retained CR bytes"
    $ssmPayloadPath = Join-Path $AcceptanceDir "ssm-payload.json"
    Write-AwsCliJsonPayload -Payload @{
        DocumentName = "AWS-RunShellScript"
        InstanceIds = @("i-selftest")
        Parameters = @{ commands = $ssmCommands; executionTimeout = @("1800") }
    } -Path $ssmPayloadPath
    $ssmPayloadBytes = [System.IO.File]::ReadAllBytes($ssmPayloadPath)
    $ssmPayloadHasBom = (
        $ssmPayloadBytes[0] -eq 0xEF -and
        $ssmPayloadBytes[1] -eq 0xBB -and
        $ssmPayloadBytes[2] -eq 0xBF
    )
    Assert-SelfTest (-not $ssmPayloadHasBom) "SSM payload JSON contained a UTF-8 BOM"
    $ssmPayloadParsed = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($ssmPayloadPath, (New-Object System.Text.UTF8Encoding($false))))
    $sentRemoteScript = [string]$ssmPayloadParsed.Parameters.commands[1]
    Assert-SelfTest ($sentRemoteScript.StartsWith("#!/usr/bin/env bash`nset -euo pipefail")) "Serialized remote script did not preserve Bash shebang and pipefail"
    Assert-SelfTest ($sentRemoteScript -notmatch "`r") "Serialized remote script contains CR bytes"
    Assert-SelfTest ($ssmPayloadParsed.Parameters.commands[4] -eq "/usr/bin/env bash /tmp/v3151_ssm_accept.sh") "Serialized SSM payload does not explicitly invoke Bash"

    Write-Host "V3.15.1 SSM bridge self-test PASS"
}

function Assert-OnlineInstance {
    param([object]$InstanceInfo)
    if (-not $InstanceInfo.InstanceInformationList -or $InstanceInfo.InstanceInformationList.Count -lt 1) {
        throw "SSM instance $InstanceId was not returned"
    }
    $item = $InstanceInfo.InstanceInformationList[0]
    if ($item.PingStatus -ne "Online") {
        throw "SSM instance $InstanceId PingStatus is $($item.PingStatus), expected Online"
    }
    return $item
}

function New-RemoteAcceptanceScript {
    @'
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/ubuntu/direction-engine-v3"
EXPECTED_MINIMUM_COMMIT="7762a0c5f239f54ca44db7b7c4e31ecfb0111dda"
PY="$PROJECT_DIR/.venv/bin/python"
ACCEPT_DIR="$PROJECT_DIR/runtime/acceptance"
REMOTE_JSON="$ACCEPT_DIR/v3151_remote_acceptance.json"
mkdir -p "$ACCEPT_DIR"

run_ubuntu() {
  sudo -H -u ubuntu bash -lc "cd '$PROJECT_DIR' && $*"
}

json_escape() {
  "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

record_step() {
  printf '\n===== %s =====\n' "$1"
}

record_step "os-user-runtime"
id
uname -a
test -x "$PY"
"$PY" --version

record_step "git-update"
cd "$PROJECT_DIR"
run_ubuntu "git fetch origin"
run_ubuntu "git pull --ff-only origin main"
DEPLOYED_COMMIT="$(run_ubuntu "git rev-parse HEAD")"
echo "deployed_commit=$DEPLOYED_COMMIT"
run_ubuntu "git merge-base --is-ancestor '$EXPECTED_MINIMUM_COMMIT' HEAD"

record_step "local-tests"
run_ubuntu "$PY -m compileall src tests"
run_ubuntu "$PY -m pytest tests/unit/test_shadow_daemon.py tests/integration/test_paper_dashboard_api.py tests/integration/test_dashboard_api.py tests/security/test_shadow_security.py -q"
run_ubuntu "$PY -m pytest -q"
run_ubuntu "$PY -m ruff check ."
run_ubuntu "$PY -m mypy src"
run_ubuntu "git diff --check"
GIT_STATUS="$(run_ubuntu "git status --short")"
if [ -n "$GIT_STATUS" ]; then
  echo "$GIT_STATUS"
  exit 12
fi

record_step "deploy-project-systemd-units"
for unit in \
  direction-engine-v3-shadow.service \
  direction-engine-v3-shadow-report.service \
  direction-engine-v3-shadow-report.timer \
  direction-engine-v3-dashboard.service
do
  test -f "$PROJECT_DIR/deploy/systemd/$unit"
  install -m 0644 "$PROJECT_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
if systemctl list-unit-files direction-engine-v3-shadow.timer >/dev/null 2>&1; then
  systemctl disable --now direction-engine-v3-shadow.timer || true
fi
rm -f /etc/systemd/system/direction-engine-v3-shadow.timer
systemctl daemon-reload
systemctl enable direction-engine-v3-dashboard.service
systemctl enable direction-engine-v3-shadow.service
systemctl enable direction-engine-v3-shadow-report.timer
systemctl restart direction-engine-v3-dashboard.service
systemctl restart direction-engine-v3-shadow.service
systemctl restart direction-engine-v3-shadow-report.timer
systemctl start direction-engine-v3-shadow-report.service || true

record_step "service-states"
systemctl is-active direction-engine-v3-dashboard.service
systemctl is-active direction-engine-v3-shadow.service
systemctl is-active direction-engine-v3-shadow-report.timer
systemctl is-enabled direction-engine-v3-dashboard.service
systemctl is-enabled direction-engine-v3-shadow.service
systemctl is-enabled direction-engine-v3-shadow-report.timer

record_step "wait-for-real-shadow-cycles"
sleep 95

collect_metrics() {
  "$PY" - <<'PY'
import json
import sqlite3
from pathlib import Path

project = Path("/home/ubuntu/direction-engine-v3")
shadow = project / "runtime/data/shadow_evidence.sqlite3"
paper = project / "runtime/data/paper.sqlite3"

result = {
    "supported_market_count": 0,
    "evaluation_count": 0,
    "abstain_count": 0,
    "paper_trade_count": 0,
    "settled_paper_trade_count": 0,
    "realized_paper_pnl": "0",
    "book_observation_count": 0,
    "proxy_observation_count": 0,
    "official_reference_observation_count": 0,
    "paper_database_path": str(paper),
    "new_evidence_window_start_utc": None,
}

if shadow.exists():
    with sqlite3.connect(shadow) as con:
        row = con.execute(
            "SELECT started_at FROM evidence_windows ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row:
            result["new_evidence_window_start_utc"] = row[0]
        for (payload_json,) in con.execute(
            "SELECT payload_json FROM shadow_events WHERE event_type='REAL_SHADOW_CYCLE'"
        ):
            payload = json.loads(payload_json)
            result["supported_market_count"] += int(payload.get("markets_discovered", 0))
            result["book_observation_count"] += int(payload.get("book_observations", 0))
            result["proxy_observation_count"] += int(payload.get("proxy_observations", 0))
            result["official_reference_observation_count"] += int(
                payload.get("official_observations", 0)
            )
            result["evaluation_count"] += int(payload.get("strategy_evaluations", 0))
            result["abstain_count"] += int(payload.get("abstain_records", 0))

if paper.exists():
    with sqlite3.connect(paper) as con:
        row = con.execute("SELECT COUNT(*) FROM paper_abstains").fetchone()
        if row:
            result["abstain_count"] = max(result["abstain_count"], int(row[0]))
        row = con.execute("SELECT COUNT(*) FROM paper_trade_snapshots").fetchone()
        if row:
            result["paper_trade_count"] = int(row[0])
        row = con.execute(
            "SELECT COUNT(*) FROM paper_trade_snapshots WHERE status='SETTLED'"
        ).fetchone()
        if row:
            result["settled_paper_trade_count"] = int(row[0])
        total = 0
        for (payload_json,) in con.execute("SELECT payload_json FROM paper_trade_snapshots"):
            payload = json.loads(payload_json)
            total += float(payload.get("realized_paper_pnl", 0) or 0)
        result["realized_paper_pnl"] = str(total)

print(json.dumps(result, sort_keys=True))
PY
}

METRICS_BEFORE_RESTART="$(collect_metrics)"
echo "metrics_before_restart=$METRICS_BEFORE_RESTART"

"$PY" "$PROJECT_DIR/scripts/vps_shadow_runtime_smoke.py"

"$PY" - "$METRICS_BEFORE_RESTART" <<'PY'
import json, sys
m = json.loads(sys.argv[1])
if int(m["supported_market_count"]) <= 0:
    raise SystemExit("supported_market_count remained zero; real public data proof failed")
if int(m["evaluation_count"]) <= 0:
    raise SystemExit("evaluation_count remained zero; strategy evaluation proof failed")
if int(m["abstain_count"]) <= 0:
    raise SystemExit("abstain_count remained zero; rejection/abstain proof failed")
PY

record_step "restart-idempotency"
PAPER_TRADES_BEFORE="$("$PY" - "$METRICS_BEFORE_RESTART" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["paper_trade_count"])
PY
)"
SETTLED_BEFORE="$("$PY" - "$METRICS_BEFORE_RESTART" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["settled_paper_trade_count"])
PY
)"
systemctl restart direction-engine-v3-shadow.service
sleep 65
systemctl is-active direction-engine-v3-shadow.service
METRICS_AFTER_RESTART="$(collect_metrics)"
echo "metrics_after_restart=$METRICS_AFTER_RESTART"
"$PY" - "$METRICS_BEFORE_RESTART" "$METRICS_AFTER_RESTART" <<'PY'
import json, sys
before = json.loads(sys.argv[1])
after = json.loads(sys.argv[2])
if int(after["evaluation_count"]) <= int(before["evaluation_count"]):
    raise SystemExit("evaluation_count did not increase after restart")
if int(after["paper_trade_count"]) < int(before["paper_trade_count"]):
    raise SystemExit("paper trade count decreased after restart")
if int(after["settled_paper_trade_count"]) < int(before["settled_paper_trade_count"]):
    raise SystemExit("settled paper count decreased after restart")
PY

record_step "dashboard-port-and-endpoints"
DASHBOARD_PORT="8130"
if curl -fsS "http://127.0.0.1:8131/health/live" >/dev/null 2>&1; then
  DASHBOARD_PORT="8131"
elif curl -fsS "http://127.0.0.1:8130/health/live" >/dev/null 2>&1; then
  DASHBOARD_PORT="8130"
else
  echo "No dashboard endpoint responded on 8131 or 8130"
  systemctl status direction-engine-v3-dashboard.service --no-pager || true
  exit 22
fi
echo "dashboard_port=$DASHBOARD_PORT"

ENDPOINT_JSON="$("$PY" - <<'PY' "$DASHBOARD_PORT"
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

port = sys.argv[1]
paths = [
    "/health/live",
    "/health/shadow-ready",
    "/health/trading-ready",
    "/api/paper/summary",
    "/api/paper/trades",
    "/api/paper/abstains",
    "/api/shadow/status",
]
results = {}
for path in paths:
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as response:
            body = response.read().decode("utf-8")
            results[path] = {"status": response.status, "contains_paper_label": "PAPER / SHADOW" in body}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        results[path] = {"status": exc.code, "contains_paper_label": "PAPER / SHADOW" in body}
    except URLError as exc:
        results[path] = {"status": "UNREACHABLE", "reason": str(exc)}
print(json.dumps(results, sort_keys=True))
PY
)"
echo "endpoint_results=$ENDPOINT_JSON"
"$PY" - "$ENDPOINT_JSON" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
if r["/health/live"]["status"] != 200:
    raise SystemExit("/health/live did not return 200")
if r["/health/shadow-ready"]["status"] != 200:
    raise SystemExit("/health/shadow-ready did not return 200")
if r["/health/trading-ready"]["status"] != 503:
    raise SystemExit("/health/trading-ready did not fail closed with 503")
for path in ("/api/paper/summary", "/api/paper/trades", "/api/paper/abstains", "/api/shadow/status"):
    if r[path]["status"] != 200:
        raise SystemExit(f"{path} did not return 200")
if not (r["/api/paper/summary"]["contains_paper_label"] or r["/api/paper/trades"]["contains_paper_label"]):
    raise SystemExit("dashboard API did not expose PAPER / SHADOW label")
PY

record_step "safety-state"
SAFETY_JSON="$(curl -fsS "http://127.0.0.1:$DASHBOARD_PORT/api/dashboard")"
"$PY" - "$SAFETY_JSON" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
mode = payload["mode"]
execution = payload["execution"]
if mode["app_mode"] != "PAPER":
    raise SystemExit("APP_MODE is not PAPER")
if mode["live_trading_enabled"] is not False:
    raise SystemExit("LIVE_TRADING_ENABLED is not false")
if mode["live_auto_arm"] is not False:
    raise SystemExit("LIVE_AUTO_ARM is not false")
if execution["real_order_submission"] is not False:
    raise SystemExit("real_order_submission is not false")
PY

SHADOW_STATE="$(systemctl is-active direction-engine-v3-shadow.service)"
REPORT_TIMER_STATE="$(systemctl is-active direction-engine-v3-shadow-report.timer)"
DASHBOARD_STATE="$(systemctl is-active direction-engine-v3-dashboard.service)"
PYTHON_VERSION="$("$PY" --version 2>&1)"
FINAL_METRICS="$METRICS_AFTER_RESTART"

"$PY" - <<'PY' \
  "$REMOTE_JSON" \
  "$DEPLOYED_COMMIT" \
  "$PYTHON_VERSION" \
  "$SHADOW_STATE" \
  "$REPORT_TIMER_STATE" \
  "$DASHBOARD_STATE" \
  "$FINAL_METRICS" \
  "$ENDPOINT_JSON" \
  "$DASHBOARD_PORT"
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    commit,
    python_version,
    shadow_state,
    report_timer_state,
    dashboard_state,
    metrics_json,
    endpoint_json,
    dashboard_port,
) = sys.argv[1:]
metrics = json.loads(metrics_json)
result = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED",
    "deployed_commit": commit,
    "python_version": python_version,
    "focused_tests": "passed",
    "full_tests": "passed",
    "ruff": "passed",
    "mypy": "passed",
    "diff_check": "passed",
    "git_status_clean": True,
    "shadow_service_state": shadow_state,
    "report_timer_state": report_timer_state,
    "dashboard_service_state": dashboard_state,
    "supported_market_count": metrics["supported_market_count"],
    "evaluation_count": metrics["evaluation_count"],
    "abstain_count": metrics["abstain_count"],
    "paper_trade_count": metrics["paper_trade_count"],
    "settled_paper_trade_count": metrics["settled_paper_trade_count"],
    "realized_paper_pnl": metrics["realized_paper_pnl"],
    "paper_database_path": metrics["paper_database_path"],
    "dashboard_port": dashboard_port,
    "endpoint_results": json.loads(endpoint_json),
    "restart_test": "passed",
    "new_evidence_window_start_utc": metrics["new_evidence_window_start_utc"],
    "app_mode": "PAPER",
    "live_trading_enabled": False,
    "live_auto_arm": False,
    "real_order_submission": False,
    "final_status": "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED",
}
Path(path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print("V3151_ACCEPTANCE_JSON_BEGIN")
print(json.dumps(result, sort_keys=True))
print("V3151_ACCEPTANCE_JSON_END")
PY
'@
}

function Send-RunCommand {
    param([string]$RemoteScript)

    $remoteScriptLf = ConvertTo-LfText -Text $RemoteScript
    $commands = @(
        "cat > /tmp/v3151_ssm_accept.sh <<'V3151_BASH'",
        $remoteScriptLf,
        "V3151_BASH",
        "chmod 700 /tmp/v3151_ssm_accept.sh",
        "/usr/bin/env bash /tmp/v3151_ssm_accept.sh"
    )
    $payload = @{
        DocumentName = "AWS-RunShellScript"
        InstanceIds = @($InstanceId)
        Comment = "direction-engineV3 V3.15.1 real shadow acceptance"
        Parameters = @{ commands = $commands; executionTimeout = @([string]$CommandTimeoutSeconds) }
    }
    $payloadPath = [System.IO.Path]::GetTempFileName()
    Write-AwsCliJsonPayload -Payload $payload -Path $payloadPath
    try {
        $response = Invoke-AwsJson -AwsArgs @(
            "ssm", "send-command",
            "--profile", $Profile,
            "--region", $Region,
            "--cli-input-json", "file://$payloadPath"
        )
        return $response.Command.CommandId
    }
    finally {
        Remove-Item -LiteralPath $payloadPath -Force -ErrorAction SilentlyContinue
    }
}

function Wait-RunCommand {
    param([string]$CommandId)
    $terminal = @("Success", "Failed", "Cancelled", "TimedOut")
    while ($true) {
        Start-Sleep -Seconds $PollSeconds
        $invocation = Invoke-AwsJson -AwsArgs @(
            "ssm", "get-command-invocation",
            "--profile", $Profile,
            "--region", $Region,
            "--command-id", $CommandId,
            "--instance-id", $InstanceId
        )
        Write-Log "SSM command $CommandId status=$($invocation.Status)"
        if ($terminal -contains $invocation.Status) {
            return $invocation
        }
    }
}

if ($SelfTest) {
    Invoke-BridgeSelfTest
    exit 0
}

$result = @{
    status = "V3.15.1_BLOCKED"
    instance_id = $InstanceId
    aws_root_profile_security_debt = $false
    final_status = "V3.15.1_BLOCKED"
}

try {
    Write-Log "Starting V3.15.1 user-context SSM acceptance bridge"
    $awsVersion = Invoke-AwsText -AwsArgs @("--version")
    Write-Log $awsVersion
    $identity = Invoke-AwsJson -AwsArgs @(
        "sts", "get-caller-identity",
        "--profile", $Profile,
        "--region", $Region
    )
    $result.caller_arn = [string]$identity.Arn
    if ($result.caller_arn -match ":root$") {
        $result.aws_root_profile_security_debt = $true
        Write-Log "AWS_ROOT_PROFILE_SECURITY_DEBT=true"
    }

    $instanceInfo = Invoke-AwsJson -AwsArgs @(
        "ssm", "describe-instance-information",
        "--profile", $Profile,
        "--region", $Region,
        "--filters", "Key=InstanceIds,Values=$InstanceId"
    )
    $instance = Assert-OnlineInstance -InstanceInfo $instanceInfo
    $result.ssm_ping_status = [string]$instance.PingStatus

    $commandId = Send-RunCommand -RemoteScript (New-RemoteAcceptanceScript)
    $result.ssm_command_id = $commandId
    Write-Log "CommandId=$commandId"
    $invocation = Wait-RunCommand -CommandId $commandId
    $stdout = [string]$invocation.StandardOutputContent
    $stderr = [string]$invocation.StandardErrorContent
    Write-LogBlock $stdout
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        Write-LogBlock "===== STDERR ====="
        Write-LogBlock $stderr
    }
    if ($invocation.Status -ne "Success") {
        throw "SSM command $commandId ended with $($invocation.Status)"
    }
    if ($stdout -notmatch "V3151_ACCEPTANCE_JSON_BEGIN\s*(\{.*\})\s*V3151_ACCEPTANCE_JSON_END") {
        throw "Acceptance JSON marker was not found in SSM stdout"
    }
    $remote = $Matches[1] | ConvertFrom-Json
    foreach ($property in $remote.PSObject.Properties) {
        $result[$property.Name] = $property.Value
    }
    $result.status = "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED"
    $result.final_status = "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED"
    $result.ssm_command_id = $commandId
    Save-Result -Result $result

    Write-Host ""
    Write-Host "V3.15_REAL_SHADOW_INFRA_ACCEPTED_EVIDENCE_RESTARTED"
    Write-Host "CommandId: $commandId"
    Write-Host "Acceptance JSON: $JsonPath"
    Write-Host "Acceptance log: $LogPath"
    Write-Host ""
    Write-Host "Dashboard SSM port-forward:"
    Write-Host "aws ssm start-session ``"
    Write-Host "  --target $InstanceId ``"
    Write-Host "  --document-name AWS-StartPortForwardingSession ``"
    Write-Host "  --parameters '{`"portNumber`":[`"$($result.dashboard_port)`"],`"localPortNumber`":[`"8130`"]}' ``"
    Write-Host "  --profile $Profile ``"
    Write-Host "  --region $Region"
    Write-Host "Browser URL: http://127.0.0.1:8130/"
    exit 0
}
catch {
    $result.error = [string]$_
    Save-Result -Result $result
    Write-Log "FAILED: $($_)"
    Write-Error $_
    exit 1
}
