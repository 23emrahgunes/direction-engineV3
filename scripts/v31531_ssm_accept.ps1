<#
V3.15.3.1 user-context SSM acceptance bridge.

Run only from the normal Windows PowerShell user context where the direction-v3
AWS profile is authenticated. Uses AWS Systems Manager Run Command only.
#>

[CmdletBinding()]
param(
    [string]$Profile = "direction-v3",
    [string]$Region = "eu-north-1",
    [string]$InstanceId = "i-0c0e730834569177e",
    [string]$ProjectDir = "/home/ubuntu/direction-engine-v3",
    [string]$ExpectedMinimumCommit = "e9d92edcb51c2e55478b4b726fee97ba74dd5478",
    [int]$PollSeconds = 10,
    [int]$CommandTimeoutSeconds = 1200,
    [switch]$SelfTest,
    [string]$AwsExecutable = "aws"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($SelfTest) {
    $AcceptanceDir = Join-Path ([System.IO.Path]::GetTempPath()) ("v31531_ssm_accept_selftest_" + [Guid]::NewGuid().ToString("N"))
} else {
    $AcceptanceDir = Join-Path $RepoRoot "runtime\acceptance"
}
$JsonPath = Join-Path $AcceptanceDir "v31531_ssm_acceptance.json"
$LogPath = Join-Path $AcceptanceDir "v31531_ssm_acceptance.log"
New-Item -ItemType Directory -Force -Path $AcceptanceDir | Out-Null
$script:AwsExecutable = $AwsExecutable

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f ([DateTimeOffset]::UtcNow.ToString("o")), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Save-Result {
    param([hashtable]$Result)
    $Result.generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    $json = $Result | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($JsonPath, $json, [System.Text.UTF8Encoding]::new($false))
}

function Write-AwsCliJsonPayload {
    param([hashtable]$Payload, [string]$Path)
    $json = $Payload | ConvertTo-Json -Depth 16
    [void](ConvertFrom-Json -InputObject $json)
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
    [byte[]]$firstBytes = [System.IO.File]::ReadAllBytes($Path) | Select-Object -First 3
    if (
        $firstBytes.Length -ge 3 -and
        $firstBytes[0] -eq 0xEF -and
        $firstBytes[1] -eq 0xBB -and
        $firstBytes[2] -eq 0xBF
    ) {
        throw "AWS CLI JSON payload was written with a UTF-8 BOM"
    }
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
    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = $script:AwsExecutable
    $processInfo.Arguments = (($AwsArgs | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $processInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $processInfo.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $processInfo
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd().Trim()
    $stderr = $process.StandardError.ReadToEnd().Trim()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            Write-Log $stderr
        }
        throw "AWS command failed ($($process.ExitCode)): aws $($AwsArgs -join ' ')`n$stdout`n$stderr"
    }
    return $stdout
}

function Invoke-AwsJson {
    param([string[]]$AwsArgs)
    $text = Invoke-AwsText -AwsArgs $AwsArgs
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw "AWS command returned empty JSON"
    }
    return ConvertFrom-Json -InputObject $text
}

function New-RemoteAcceptanceScript {
@'
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/ubuntu/direction-engine-v3"
PY="$PROJECT_DIR/.venv/bin/python"
ACCEPT_DIR="$PROJECT_DIR/runtime/acceptance"
REMOTE_JSON="$ACCEPT_DIR/v31531_remote_acceptance.json"
EXPECTED_MINIMUM_COMMIT="__EXPECTED_MINIMUM_COMMIT__"
mkdir -p "$ACCEPT_DIR"

run_ubuntu() {
  sudo -H -u ubuntu bash -lc "cd '$PROJECT_DIR' && $*"
}

record_step() {
  printf '\n===== %s =====\n' "$1"
}

cycle_count() {
  run_ubuntu "$PY - <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print(0)
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute(\"SELECT COUNT(*) FROM shadow_events WHERE event_type='REAL_SHADOW_CYCLE'\").fetchone()
    print(int(row[0] or 0))
PY"
}

latest_cycle_ts() {
  run_ubuntu "$PY - <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print('')
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute(\"SELECT observed_at FROM shadow_events WHERE event_type='REAL_SHADOW_CYCLE' ORDER BY observed_at DESC LIMIT 1\").fetchone()
    print('' if row is None else row[0])
PY"
}

record_step "git-update"
run_ubuntu "git fetch origin"
run_ubuntu "git pull --ff-only origin main"
DEPLOYED_COMMIT="$(run_ubuntu "git rev-parse HEAD")"
run_ubuntu "git merge-base --is-ancestor '$EXPECTED_MINIMUM_COMMIT' HEAD"

record_step "tests"
run_ubuntu "$PY -m compileall src tests"
run_ubuntu "$PY -m pytest tests/unit/test_v31531_structural_runtime.py tests/unit/test_v31531_diagnostics.py tests/unit/test_v3153_official_ptb_runtime.py tests/integration/test_dashboard_api.py tests/security/test_shadow_security.py -q"
run_ubuntu "$PY -m pytest -q"
run_ubuntu "$PY -m ruff check ."
run_ubuntu "$PY -m mypy src"
run_ubuntu "git diff --check"
GIT_STATUS="$(run_ubuntu "git status --short")"
if [ -n "$GIT_STATUS" ]; then
  echo "$GIT_STATUS"
  exit 12
fi

record_step "deploy-project-services"
for unit in \
  direction-engine-v3-shadow.service \
  direction-engine-v3-shadow-report.service \
  direction-engine-v3-shadow-report.timer \
  direction-engine-v3-dashboard.service
do
  test -f "$PROJECT_DIR/deploy/systemd/$unit"
  install -m 0644 "$PROJECT_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable direction-engine-v3-dashboard.service
systemctl enable direction-engine-v3-shadow.service
systemctl enable direction-engine-v3-shadow-report.timer
systemctl restart direction-engine-v3-dashboard.service
systemctl restart direction-engine-v3-shadow.service
systemctl restart direction-engine-v3-shadow-report.timer

record_step "daemon-cycle-advancement"
systemctl is-active --quiet direction-engine-v3-shadow.service
PID_BEFORE="$(systemctl show -p MainPID --value direction-engine-v3-shadow.service)"
RESTARTS_BEFORE="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
CYCLES_BEFORE="$(cycle_count)"
TS_BEFORE="$(latest_cycle_ts)"
sleep 90
systemctl is-active --quiet direction-engine-v3-shadow.service
PID_AFTER="$(systemctl show -p MainPID --value direction-engine-v3-shadow.service)"
RESTARTS_AFTER="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
CYCLES_AFTER="$(cycle_count)"
TS_AFTER="$(latest_cycle_ts)"
if [ "$CYCLES_AFTER" -le "$CYCLES_BEFORE" ] || [ "$TS_AFTER" = "$TS_BEFORE" ]; then
  FINAL_STATUS="V3.15.3.1_BLOCKED"
  BLOCK_REASON="SHADOW_DAEMON_NOT_ADVANCING"
else
  FINAL_STATUS="V3.15.3.1_BLOCKED"
  BLOCK_REASON="PTB_READY_NOT_OBSERVED_BEFORE_TIMEOUT"
fi

record_step "diagnostic-probes"
run_ubuntu "$PY -m direction_engine_v3.diagnostics.official_source_audit --output runtime/reports/v31531_official_source_audit.json"
run_ubuntu "$PY -m direction_engine_v3.diagnostics.rtds_probe --duration-seconds 30 --output runtime/reports/v31531_rtds_probe.json"
run_ubuntu "$PY -m direction_engine_v3.diagnostics.binance_hourly_probe --output runtime/reports/v31531_binance_hourly_probe.json"

record_step "bounded-5m-boundary-proof"
LAST_DIRECTIONAL_JSON="/tmp/v31531_directional.json"
DEADLINE=$(( $(date +%s) + 600 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  sleep 15
  curl -fsS http://127.0.0.1:8130/api/directional/status > "$LAST_DIRECTIONAL_JSON"
  READINESS="$("$PY" - "$LAST_DIRECTIONAL_JSON" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding='utf-8'))
buckets = payload.get('buckets', [])
ready = any(
    item.get('horizon') == '5m'
    and item.get('official_status') == 'OFFICIAL_REFERENCE_READY'
    and item.get('ptb_status') == 'PTB_READY'
    and item.get('ptb_value') not in {None, '0', 0}
    and item.get('proxy_status') in {'PROXY_READY', 'PROXY_UNAVAILABLE'}
    and (item.get('chainlink') or {}).get('last_source_timestamp')
    for item in buckets
)
print('true' if ready else 'false')
PY
)"
  if [ "$READINESS" = "true" ] && [ "$BLOCK_REASON" != "SHADOW_DAEMON_NOT_ADVANCING" ]; then
    FINAL_STATUS="V3.15.3.1_OFFICIAL_PTB_ACTIVE"
    BLOCK_REASON=""
    break
  fi
done

record_step "dashboard-proof"
curl -fsS http://127.0.0.1:8130/api/directional/status >/tmp/v31531_directional_final.json
curl -fsS http://127.0.0.1:8130/ >/tmp/v31531_dashboard.html
grep -q "PAPER / SHADOW" /tmp/v31531_dashboard.html

record_step "write-evidence"
"$PY" - "$REMOTE_JSON" "$DEPLOYED_COMMIT" "$FINAL_STATUS" "$BLOCK_REASON" "$PID_BEFORE" "$PID_AFTER" "$RESTARTS_BEFORE" "$RESTARTS_AFTER" "$CYCLES_BEFORE" "$CYCLES_AFTER" "$TS_BEFORE" "$TS_AFTER" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    commit,
    status,
    blocker,
    pid_before,
    pid_after,
    restarts_before,
    restarts_after,
    cycles_before,
    cycles_after,
    ts_before,
    ts_after,
) = sys.argv[1:]

def read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        return {'read_error': type(exc).__name__}

result = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': status,
    'final_status': status,
    'blocker': blocker or None,
    'deployed_commit': commit,
    'compileall': 'passed',
    'focused_tests': 'passed',
    'full_tests': 'passed',
    'ruff': 'passed',
    'mypy': 'passed',
    'diff_check': 'passed',
    'git_status_clean': True,
    'daemon': {
        'pid_before': pid_before,
        'pid_after': pid_after,
        'restarts_before': restarts_before,
        'restarts_after': restarts_after,
        'cycles_before': cycles_before,
        'cycles_after': cycles_after,
        'latest_cycle_before': ts_before,
        'latest_cycle_after': ts_after,
    },
    'official_source_audit': read_json('runtime/reports/v31531_official_source_audit.json'),
    'rtds_probe': read_json('runtime/reports/v31531_rtds_probe.json'),
    'binance_hourly_probe': read_json('runtime/reports/v31531_binance_hourly_probe.json'),
    'directional_status': read_json('/tmp/v31531_directional_final.json'),
    'app_mode': 'PAPER',
    'live_trading_enabled': False,
    'live_auto_arm': False,
    'real_order_submission': False,
}
Path(path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
print('V31531_ACCEPTANCE_JSON_BEGIN')
print(json.dumps(result, sort_keys=True))
print('V31531_ACCEPTANCE_JSON_END')
PY
'@
}

function Assert-SelfTest {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw "Self-test failed: $Message"
    }
}

function Invoke-BridgeSelfTest {
    $captured = @(Write-Log "unicode self-test PAPER / SHADOW $([char]8212) NO REAL ORDER")
    Assert-SelfTest ($captured.Count -eq 0) "Write-Log emitted success output"
    $payloadPath = Join-Path $AcceptanceDir "payload.json"
    $selfTestPayload = @{
        DocumentName = "AWS-RunShellScript"
        InstanceIds = @("i-selftest")
        Parameters = @{ commands = @("echo PAPER") }
    }
    Write-AwsCliJsonPayload -Payload $selfTestPayload -Path $payloadPath
    $bytes = [System.IO.File]::ReadAllBytes($payloadPath)
    Assert-SelfTest (-not ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) "payload had BOM"
    $remote = (New-RemoteAcceptanceScript).Replace("__EXPECTED_MINIMUM_COMMIT__", $ExpectedMinimumCommit)
    Assert-SelfTest ($remote.StartsWith("#!/usr/bin/env bash`nset -euo pipefail")) "remote script is not explicit Bash"
    Assert-SelfTest ($remote -match "direction_engine_v3.diagnostics.rtds_probe") "RTDS probe missing"
    Assert-SelfTest ($remote -match "SHADOW_DAEMON_NOT_ADVANCING") "daemon advancement blocker missing"
    Assert-SelfTest ($remote -match "V3.15.3.1_OFFICIAL_PTB_ACTIVE") "success status missing"
    $lf = ConvertTo-LfText -Text "a`r`nb`rc`n"
    Assert-SelfTest ($lf -eq "a`nb`nc`n") "LF normalization failed"
    Assert-SelfTest ($remote -notmatch "private_key|api_secret|LIVE_TRADING_ENABLED=true|LIVE_AUTO_ARM=true") "unsafe text found"
    Write-Host "V3.15.3.1 SSM bridge self-test PASS"
}

function Send-RunCommand {
    param([string]$RemoteScript)
    $remoteScriptLf = ConvertTo-LfText -Text $RemoteScript
    $commands = @(
        "cat > /tmp/v31531_ssm_accept.sh <<'V31531_BASH'",
        $remoteScriptLf,
        "V31531_BASH",
        "chmod 700 /tmp/v31531_ssm_accept.sh",
        "/usr/bin/env bash /tmp/v31531_ssm_accept.sh"
    )
    $payload = @{
        DocumentName = "AWS-RunShellScript"
        InstanceIds = @($InstanceId)
        Comment = "direction-engineV3 V3.15.3.1 official PTB source acceptance"
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
    } finally {
        Remove-Item -LiteralPath $payloadPath -Force -ErrorAction SilentlyContinue
    }
    return [string]$response.Command.CommandId
}

function Wait-RunCommand {
    param([string]$CommandId)
    $terminal = @("Success", "Cancelled", "TimedOut", "Failed", "Cancelling")
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
    status = "V3.15.3.1_BLOCKED"
    final_status = "V3.15.3.1_BLOCKED"
    instance_id = $InstanceId
    aws_root_profile_security_debt = $false
}

try {
    Write-Log "Starting V3.15.3.1 user-context SSM acceptance bridge"
    $identity = Invoke-AwsJson -AwsArgs @("sts", "get-caller-identity", "--profile", $Profile, "--region", $Region)
    $result.caller_arn = [string]$identity.Arn
    if ($result.caller_arn -match ":root$") {
        $result.aws_root_profile_security_debt = $true
        Write-Log "AWS_ROOT_PROFILE_SECURITY_DEBT=true"
    }
    $instanceInfo = Invoke-AwsJson -AwsArgs @("ssm", "describe-instance-information", "--profile", $Profile, "--region", $Region, "--filters", "Key=InstanceIds,Values=$InstanceId")
    if (-not $instanceInfo.InstanceInformationList -or $instanceInfo.InstanceInformationList[0].PingStatus -ne "Online") {
        throw "SSM instance is not Online"
    }
    $remote = (New-RemoteAcceptanceScript).Replace("__EXPECTED_MINIMUM_COMMIT__", $ExpectedMinimumCommit)
    $commandId = Send-RunCommand -RemoteScript $remote
    $result.command_id = $commandId
    $invocation = Wait-RunCommand -CommandId $commandId
    $result.ssm_status = [string]$invocation.Status
    $result.stdout = [string]$invocation.StandardOutputContent
    $result.stderr = [string]$invocation.StandardErrorContent
    if ($invocation.StandardOutputContent -match "V31531_ACCEPTANCE_JSON_BEGIN\s*(\{.*\})\s*V31531_ACCEPTANCE_JSON_END") {
        $remoteResult = ConvertFrom-Json -InputObject $Matches[1]
        $result.remote = $remoteResult
        $result.status = [string]$remoteResult.status
        $result.final_status = [string]$remoteResult.final_status
    }
    if ($invocation.Status -ne "Success") {
        throw "SSM command did not succeed: $($invocation.Status)"
    }
    Save-Result -Result $result
    Write-Log "V3.15.3.1 acceptance completed with status=$($result.final_status)"
} catch {
    $result.error = $_.Exception.Message
    Save-Result -Result $result
    Write-Log $result.error
    throw
}
