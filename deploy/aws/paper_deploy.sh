#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SHA="${1:?expected git SHA is required}"
PROJECT_DIR="${2:-/home/ubuntu/direction-engine-v3}"
PY="$PROJECT_DIR/.venv/bin/python"
DEPLOY_STATE_DIR="/var/lib/direction-engine-v3/deploy"
LEGACY_DEPLOY_STATE_DIR="$PROJECT_DIR/runtime/deploy"
PAPER_ARCHIVE_DIR="$PROJECT_DIR/runtime/archive"
DEPLOY_RESULT="$DEPLOY_STATE_DIR/github-paper-deploy-result.json"
STAGE="start"
DEPLOY_STARTED_AT="$(date -u +%FT%TZ)"

PROJECT_UNITS=(
  "direction-engine-v3-shadow.service"
  "direction-engine-v3-dashboard.service"
  "direction-engine-v3-shadow-report.service"
  "direction-engine-v3-shadow-report.timer"
)

log() {
  printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"
}

run_ubuntu() {
  sudo -H -u ubuntu bash -lc "cd '$PROJECT_DIR' && $*"
}

run_ubuntu_python() {
  sudo -H -u ubuntu bash -lc "cd '$PROJECT_DIR' && '$PY' -"
}

deploy_git_status() {
  run_ubuntu "git status --short -- . ':(exclude)runtime/archive'"
}

dump_failure_context() {
  local exit_code="$1"
  log "DEPLOY_FAILED stage=$STAGE exit_code=$exit_code expected_sha=$EXPECTED_SHA deploy_started_at=$DEPLOY_STARTED_AT"
  for unit in "${PROJECT_UNITS[@]}"; do
    systemctl show -p Id -p ActiveState -p SubState -p MainPID -p NRestarts -p InvocationID "$unit" || true
    systemctl --no-pager --full status "$unit" || true
    journalctl -u "$unit" --since "$DEPLOY_STARTED_AT" --no-pager -n 120 || true
  done
}

trap 'dump_failure_context "$?"' ERR

require_safe_environment() {
  STAGE="preflight"
  test -d "$PROJECT_DIR"
  test -x "$PY"
  case "$EXPECTED_SHA" in
    (*[!0-9a-fA-F]* | "" ) echo "Invalid expected SHA: $EXPECTED_SHA" >&2; exit 10 ;;
  esac

  cleanup_legacy_deploy_state
  cd "$PROJECT_DIR"
  local dirty_status
  dirty_status="$(deploy_git_status)"
  if [ "$dirty_status" != "" ]; then
    echo "Project working tree is dirty; refusing exact-SHA deploy" >&2
    printf '%s\n' "$dirty_status"
    exit 11
  fi
  mkdir -p "$DEPLOY_STATE_DIR"
}

cleanup_legacy_deploy_state() {
  if [ ! -e "$LEGACY_DEPLOY_STATE_DIR" ] && [ ! -L "$LEGACY_DEPLOY_STATE_DIR" ]; then
    return 0
  fi

  local project_real runtime_real legacy_real expected_legacy
  project_real="$(realpath -e "$PROJECT_DIR")"
  runtime_real="$(realpath -e "$project_real/runtime")"
  legacy_real="$(realpath -e "$LEGACY_DEPLOY_STATE_DIR")"
  expected_legacy="$runtime_real/deploy"

  if [ -z "$project_real" ] || [ -z "$runtime_real" ] || [ -z "$legacy_real" ] \
    || [ "$legacy_real" != "$expected_legacy" ] \
    || [ "$legacy_real" = "/" ] \
    || [ "$legacy_real" = "$project_real" ] \
    || [ "$legacy_real" = "$runtime_real" ]; then
    echo "Refusing unsafe legacy deploy-state cleanup: $LEGACY_DEPLOY_STATE_DIR" >&2
    exit 13
  fi

  log "removing known legacy deploy state: $legacy_real"
  rm -rf -- "$legacy_real"
}

checkout_exact_sha() {
  STAGE="git-checkout"
  run_ubuntu "git fetch --prune origin"
  run_ubuntu "git cat-file -e '$EXPECTED_SHA^{commit}'"
  run_ubuntu "git checkout --detach '$EXPECTED_SHA'"
  run_ubuntu "git branch -f main '$EXPECTED_SHA'"
  run_ubuntu "git checkout main"
  DEPLOYED_SHA="$(run_ubuntu "git rev-parse HEAD")"
  if [ "$DEPLOYED_SHA" != "$EXPECTED_SHA" ]; then
    echo "Deployed SHA mismatch: deployed=$DEPLOYED_SHA expected=$EXPECTED_SHA" >&2
    exit 12
  fi
}

install_dependencies_if_needed() {
  STAGE="dependencies"
  local current_hash previous_hash hash_file
  hash_file="$DEPLOY_STATE_DIR/requirements.sha256"
  current_hash="$(
    run_ubuntu "sha256sum pyproject.toml requirements.txt requirements-dev.txt requirements-live.txt 2>/dev/null | sha256sum | awk '{print \$1}'"
  )"
  previous_hash=""
  if [ -f "$hash_file" ]; then
    previous_hash="$(cat "$hash_file")"
  fi
  if [ "$current_hash" != "$previous_hash" ]; then
    run_ubuntu "$PY -m pip install --upgrade pip"
    run_ubuntu "$PY -m pip install -r requirements-dev.txt"
    printf '%s\n' "$current_hash" > "$hash_file"
    chown ubuntu:ubuntu "$hash_file"
  else
    log "dependency hash unchanged; skipping pip install"
  fi
}

validate_on_vps() {
  STAGE="vps-validation"
  run_ubuntu "$PY --version"
  run_ubuntu_python <<'PY'
import sys
if sys.version_info[:2] != (3, 12):
    raise SystemExit(f'Python must be 3.12.x, got {sys.version}')
PY
  run_ubuntu "$PY -m compileall src tests"
  run_ubuntu "$PY -m pytest tests/unit/test_v31531_structural_runtime.py tests/unit/test_v31531_diagnostics.py tests/security/test_shadow_security.py tests/security/test_github_actions_cicd.py -q"
  run_ubuntu "$PY -m ruff check ."
  run_ubuntu "$PY -m mypy src"
  run_ubuntu "git diff --check"
}

install_project_units() {
  STAGE="systemd-install"
  for unit in "${PROJECT_UNITS[@]}"; do
    test -f "$PROJECT_DIR/deploy/systemd/$unit"
    install -m 0644 "$PROJECT_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
  systemctl enable direction-engine-v3-dashboard.service
  systemctl enable direction-engine-v3-shadow.service
  systemctl enable direction-engine-v3-shadow-report.timer
}

stop_project_runtime_units() {
  STAGE="systemd-stop-project"
  systemctl stop direction-engine-v3-shadow-report.timer || true
  systemctl stop direction-engine-v3-shadow-report.service || true
  systemctl stop direction-engine-v3-dashboard.service || true
  systemctl stop direction-engine-v3-shadow.service || true
}

require_active_unit() {
  local unit="$1"
  local failure_exit="${2:-31}"
  if systemctl is-active --quiet "$unit"; then
    return 0
  fi
  echo "Required project unit is not active: $unit" >&2
  systemctl show -p Id -p ActiveState -p SubState -p MainPID -p NRestarts -p InvocationID "$unit" || true
  systemctl --no-pager --full status "$unit" || true
  journalctl -u "$unit" --since "$DEPLOY_STARTED_AT" --no-pager -n 120 || true
  exit "$failure_exit"
}

start_shadow_and_wait_ready() {
  STAGE="shadow-startup"
  local shadow_started_at
  shadow_started_at="$(date -u +%FT%TZ)"
  systemctl restart direction-engine-v3-shadow.service
  wait_shadow_startup_ready "$shadow_started_at"
}

start_dashboard_and_report() {
  STAGE="dashboard-report-start"
  systemctl reset-failed direction-engine-v3-dashboard.service || true
  systemctl restart direction-engine-v3-dashboard.service
  require_active_unit direction-engine-v3-dashboard.service 31
  systemctl reset-failed direction-engine-v3-shadow-report.timer direction-engine-v3-shadow-report.service || true
  if ! systemctl restart direction-engine-v3-shadow-report.timer; then
    log "WARN shadow report timer did not start during deploy; continuing with shadow/dashboard smoke"
    systemctl --no-pager --full status direction-engine-v3-shadow-report.timer || true
    journalctl -u direction-engine-v3-shadow-report.timer --since "$DEPLOY_STARTED_AT" --no-pager -n 80 || true
  fi
}

shadow_startup_ready() {
  local started_after="$1"
  sudo -H -u ubuntu bash -lc "cd '$PROJECT_DIR' && '$PY' - '$EXPECTED_SHA' '$started_after'" <<'PY'
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

expected_sha, started_after_raw = sys.argv[1:]
db_path = Path("runtime/data/shadow_evidence.sqlite3")

def parse_ts(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

started_after = parse_ts(started_after_raw)
if started_after is None:
    print(json.dumps({"status": "INVALID_DEPLOY_START", "value": started_after_raw}))
    raise SystemExit(2)
if not db_path.exists():
    print(json.dumps({"status": "WAITING_FOR_SHADOW_DB", "path": str(db_path)}))
    raise SystemExit(1)
with sqlite3.connect(db_path, timeout=1.0) as connection:
    row = connection.execute(
        "SELECT window_id, started_at, payload_json FROM evidence_windows "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
if row is None:
    print(json.dumps({"status": "WAITING_FOR_EVIDENCE_WINDOW"}))
    raise SystemExit(1)
window_id, started_at_raw, payload_json = row
started_at = parse_ts(str(started_at_raw))
payload = json.loads(str(payload_json))
fingerprint = payload.get("fingerprint", {}) if isinstance(payload, dict) else {}
code_commit = fingerprint.get("code_commit") if isinstance(fingerprint, dict) else None
result = {
    "status": "SHADOW_STARTUP_READY",
    "window_id": window_id,
    "started_at": started_at_raw,
    "code_commit": code_commit,
}
if started_at is None or started_at < started_after:
    result["status"] = "WAITING_FOR_CURRENT_EVIDENCE_WINDOW"
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(1)
if code_commit != expected_sha:
    result["status"] = "WAITING_FOR_EXPECTED_SHA_EVIDENCE_WINDOW"
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(1)
print(json.dumps(result, sort_keys=True))
PY
}

wait_shadow_startup_ready() {
  local started_after="$1"
  local deadline=$((SECONDS + 75))
  local last_result=""
  while [ "$SECONDS" -le "$deadline" ]; do
    if last_result="$(shadow_startup_ready "$started_after")"; then
      log "shadow startup readiness committed: $last_result"
      systemctl is-active --quiet direction-engine-v3-shadow.service
      return 0
    fi
    log "waiting for shadow startup evidence: $last_result"
    if ! systemctl is-active --quiet direction-engine-v3-shadow.service; then
      echo "Shadow service stopped before startup evidence was committed" >&2
      exit 23
    fi
    sleep 3
  done
  echo "Shadow startup evidence was not committed within bounded wait: $last_result" >&2
  exit 23
}

cycle_count() {
  run_ubuntu_python <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print(0)
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM shadow_events WHERE event_type='REAL_SHADOW_CYCLE'").fetchone()
    print(int(row[0] or 0))
PY
}

latest_cycle_ts() {
  run_ubuntu_python <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print('')
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT observed_at FROM shadow_events WHERE event_type='REAL_SHADOW_CYCLE' ORDER BY observed_at DESC LIMIT 1").fetchone()
    print('' if row is None else row[0])
PY
}

settlement_scan_count() {
  run_ubuntu_python <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print(0)
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM shadow_events WHERE event_type='PAPER_SETTLEMENT_SCAN'").fetchone()
    print(int(row[0] or 0))
PY
}

latest_settlement_scan_ts() {
  run_ubuntu_python <<'PY'
from pathlib import Path
import sqlite3
path = Path('runtime/data/shadow_evidence.sqlite3')
if not path.exists():
    print('')
else:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT observed_at FROM shadow_events WHERE event_type='PAPER_SETTLEMENT_SCAN' ORDER BY observed_at DESC LIMIT 1").fetchone()
    print('' if row is None else row[0])
PY
}

probe_dashboard_endpoint() {
  local name="$1" url="$2" output_path="$3"
  local started_at elapsed_ms status_code curl_exit
  started_at="$(date +%s%3N)"
  set +e
  status_code="$(curl -sS --max-time 8 -o "$output_path" -w "%{http_code}" "$url" 2>"/tmp/direction-engine-v3-${name}.err")"
  curl_exit="$?"
  set -e
  elapsed_ms="$(( $(date +%s%3N) - started_at ))"
  "$PY" - "$name" "$url" "$output_path" "/tmp/direction-engine-v3-${name}.err" "$status_code" "$curl_exit" "$elapsed_ms" <<'PY'
import json
import sys
from pathlib import Path

name, url, output_path, error_path, status_code, curl_exit, elapsed_ms = sys.argv[1:]
body_path = Path(output_path)
err_path = Path(error_path)
result = {
    "name": name,
    "url": url,
    "status_code": status_code,
    "curl_exit": int(curl_exit),
    "elapsed_ms": int(elapsed_ms),
    "bytes": body_path.stat().st_size if body_path.exists() else 0,
    "error": err_path.read_text(encoding="utf-8", errors="replace").strip()[:400]
    if err_path.exists()
    else "",
}
Path(f"/tmp/direction-engine-v3-endpoint-{name}.json").write_text(
    json.dumps(result, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(json.dumps(result, sort_keys=True))
if result["curl_exit"] != 0 or not str(status_code).startswith(("2", "3")):
    raise SystemExit(1)
PY
}

smoke_check() {
  STAGE="fast-smoke"
  require_active_unit direction-engine-v3-shadow.service 30
  require_active_unit direction-engine-v3-dashboard.service 31
  local restarts_before restarts_after cycles_before cycles_after cycle_ts_before cycle_ts_after
  local settlement_before settlement_after settlement_ts_before settlement_ts_after
  local chainlink_gate_status smoke_started_at runtime_health_status
  smoke_started_at="$(date -u +%FT%TZ)"
  restarts_before="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
  cycles_before="$(cycle_count)"
  cycle_ts_before="$(latest_cycle_ts)"
  settlement_before="$(settlement_scan_count)"
  settlement_ts_before="$(latest_settlement_scan_ts)"
  sleep 45
  require_active_unit direction-engine-v3-shadow.service 30
  require_active_unit direction-engine-v3-dashboard.service 31
  restarts_after="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
  cycles_after="$(cycle_count)"
  cycle_ts_after="$(latest_cycle_ts)"
  settlement_after="$(settlement_scan_count)"
  settlement_ts_after="$(latest_settlement_scan_ts)"
  if [ "$restarts_after" != "$restarts_before" ]; then
    echo "Shadow service restart count changed during smoke" >&2
    exit 20
  fi
  if [ "$cycles_after" -le "$cycles_before" ] || [ "$cycle_ts_after" = "$cycle_ts_before" ]; then
    echo "Shadow daemon did not advance a REAL_SHADOW_CYCLE during smoke" >&2
    exit 21
  fi
  if [ "$settlement_after" -le "$settlement_before" ] || [ "$settlement_ts_after" = "$settlement_ts_before" ]; then
    echo "Shadow daemon did not advance a PAPER_SETTLEMENT_SCAN during smoke" >&2
    exit 24
  fi
  probe_dashboard_endpoint "root" "http://127.0.0.1:8130/" "/tmp/direction-engine-v3-dashboard.html"
  probe_dashboard_endpoint "dashboard" "http://127.0.0.1:8130/api/dashboard" "/tmp/direction-engine-v3-dashboard.json"
  probe_dashboard_endpoint "paper-summary" "http://127.0.0.1:8130/api/paper/summary" "/tmp/direction-engine-v3-paper-summary.json"
  probe_dashboard_endpoint "directional" "http://127.0.0.1:8130/api/directional/status" "/tmp/direction-engine-v3-directional.json"
  probe_dashboard_endpoint "shadow" "http://127.0.0.1:8130/api/shadow/status" "/tmp/direction-engine-v3-shadow.json"
  grep -q "PAPER / SHADOW" /tmp/direction-engine-v3-dashboard.html
  chainlink_gate_status="$(chainlink_gate "$smoke_started_at")"
  runtime_health_status="$chainlink_gate_status"
  run_ubuntu_python <<'PY'
from direction_engine_v3.config import APP_MODE, LIVE_AUTO_ARM, LIVE_TRADING_ENABLED
if APP_MODE != 'PAPER' or LIVE_TRADING_ENABLED or LIVE_AUTO_ARM:
    raise SystemExit('PAPER/LIVE safety defaults violated')
PY
  if [ "$runtime_health_status" = "CHAINLINK_ACCEPTED" ]; then
    runtime_health_status="RUNTIME_HEALTH_OK"
  elif [ "$runtime_health_status" = "WAITING_FOR_FRESH_PIPELINE_EVIDENCE" ]; then
    runtime_health_status="RUNTIME_HEALTH_WARN"
  else
    runtime_health_status="RUNTIME_HEALTH_BLOCKED"
  fi
  write_result "$restarts_before" "$restarts_after" "$cycles_before" "$cycles_after" "$cycle_ts_before" "$cycle_ts_after" "$smoke_started_at" "$settlement_before" "$settlement_after" "$settlement_ts_before" "$settlement_ts_after" "$runtime_health_status"
}

chainlink_gate() {
  local smoke_started_at="$1"
  "$PY" - /tmp/direction-engine-v3-directional.json /tmp/direction-engine-v3-chainlink-gate.json "$smoke_started_at" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

directional_path, gate_path, smoke_started_at_raw = sys.argv[1:]

def parse_timestamp(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = raw.replace('Z', '+00:00')
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

smoke_started_at = parse_timestamp(smoke_started_at_raw)
if smoke_started_at is None:
    raise SystemExit(f'invalid smoke start timestamp: {smoke_started_at_raw!r}')
payload = json.loads(Path(directional_path).read_text(encoding='utf-8'))
buckets = payload.get('buckets', []) if isinstance(payload, dict) else []
fresh_candidates = []
stale_candidates = []
for item in buckets:
    if not isinstance(item, dict) or not item.get('chainlink'):
        continue
    observed_raw = (
        item.get('latest_observed_at')
        or item.get('pipeline_observed_at')
        or item.get('last_observed_at')
    )
    observed_at = parse_timestamp(observed_raw)
    if observed_at is not None and observed_at >= smoke_started_at:
        fresh_candidates.append((observed_at, item))
    else:
        stale_candidates.append(
            {
                'asset': item.get('asset'),
                'horizon': item.get('horizon'),
                'observed_at': observed_raw,
            }
        )
if not fresh_candidates:
    result = {
        'status': 'WAITING_FOR_FRESH_PIPELINE_EVIDENCE',
        'smoke_started_at': smoke_started_at_raw,
        'stale_candidates': stale_candidates,
    }
    Path(gate_path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
    print(result['status'])
    raise SystemExit(0)
required = ('BTC', 'ETH', 'SOL', 'XRP')
fresh_assets = {
    str(item.get('asset'))
    for _observed_at, item in fresh_candidates
    if item.get('asset') is not None
}
missing_fresh_assets = [asset for asset in required if asset not in fresh_assets]
if missing_fresh_assets:
    result = {
        'status': 'WAITING_FOR_FRESH_PIPELINE_EVIDENCE',
        'smoke_started_at': smoke_started_at_raw,
        'missing_fresh_assets': missing_fresh_assets,
        'fresh_assets': sorted(fresh_assets),
        'stale_candidates': stale_candidates,
    }
    Path(gate_path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
    print(result['status'])
    raise SystemExit(0)
fresh_candidates.sort(key=lambda candidate: candidate[0], reverse=True)
fresh_bucket = fresh_candidates[0][1]
collector = fresh_bucket.get('chainlink', {})
per_asset = collector.get('per_asset', {}) if isinstance(collector, dict) else {}
failures = []
for asset in required:
    state = per_asset.get(asset, {}) if isinstance(per_asset, dict) else {}
    if state.get('connection') != 'connected':
        failures.append(f'{asset}:connection={state.get("connection")}')
    if state.get('subscription_status') != 'SUBSCRIBED':
        failures.append(f'{asset}:subscription_status={state.get("subscription_status")}')
    if int(state.get('parse_success_count') or 0) <= 0:
        failures.append(f'{asset}:parse_success_count={state.get("parse_success_count")}')
    if int(state.get('history_size') or 0) <= 0:
        failures.append(f'{asset}:history_size={state.get("history_size")}')
    if state.get('last_message_at') is None:
        failures.append(f'{asset}:last_message_at=null')
    if int(state.get('subscription_snapshot_count') or 0) < 1:
        failures.append(
            f'{asset}:subscription_snapshot_count={state.get("subscription_snapshot_count")}'
        )
    if not state.get('last_frame_class'):
        failures.append(f'{asset}:last_frame_class={state.get("last_frame_class")}')
status = 'CHAINLINK_ACCEPTED' if not failures else 'CHAINLINK_RUNTIME_BLOCKED'
result = {
    'status': status,
    'smoke_started_at': smoke_started_at_raw,
    'fresh_observed_at': fresh_candidates[0][0].isoformat(),
    'fresh_bucket': {
        'asset': fresh_bucket.get('asset'),
        'horizon': fresh_bucket.get('horizon'),
        'latest_observed_at': fresh_bucket.get('latest_observed_at'),
        'pipeline_observed_at': fresh_bucket.get('pipeline_observed_at'),
        'last_observed_at': fresh_bucket.get('last_observed_at'),
    },
    'required_assets': list(required),
    'failures': failures,
    'per_asset': per_asset,
}
Path(gate_path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
print(status)
PY
}

write_result() {
  STAGE="write-result"
  local restarts_before="$1" restarts_after="$2" cycles_before="$3" cycles_after="$4"
  local cycle_ts_before="$5" cycle_ts_after="$6"
  local smoke_started_at="$7" settlement_before="$8" settlement_after="$9"
  local settlement_ts_before="${10}" settlement_ts_after="${11}" runtime_health_status="${12}"
  "$PY" - "$DEPLOY_RESULT" "$EXPECTED_SHA" "$restarts_before" "$restarts_after" "$cycles_before" "$cycles_after" "$cycle_ts_before" "$cycle_ts_after" "$smoke_started_at" "$settlement_before" "$settlement_after" "$settlement_ts_before" "$settlement_ts_after" "$runtime_health_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    expected_sha,
    rb,
    ra,
    cb,
    ca,
    tb,
    ta,
    smoke_started_at,
    settlement_before,
    settlement_after,
    settlement_ts_before,
    settlement_ts_after,
    runtime_health_status,
) = sys.argv[1:]

def read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        return {'read_error': type(exc).__name__}

directional = read_json('/tmp/direction-engine-v3-directional.json')
chainlink_gate = read_json('/tmp/direction-engine-v3-chainlink-gate.json')
endpoint_names = ('root', 'dashboard', 'paper-summary', 'directional', 'shadow')
endpoints = {
    name: read_json(f'/tmp/direction-engine-v3-endpoint-{name}.json')
    for name in endpoint_names
}
paper_summary = read_json('/tmp/direction-engine-v3-paper-summary.json')
shadow_status = read_json('/tmp/direction-engine-v3-shadow.json')
buckets = directional.get('buckets', []) if isinstance(directional, dict) else []
ptb_ready = [
    {
        'asset': item.get('asset'),
        'horizon': item.get('horizon'),
        'ptb_status': item.get('ptb_status'),
        'ptb_value': item.get('ptb_value'),
    }
    for item in buckets
    if isinstance(item, dict) and item.get('ptb_status') == 'PTB_READY'
]
collector = next((item for item in buckets if isinstance(item, dict) and item.get('chainlink')), {})
chainlink_gate_status = (
    chainlink_gate.get('status')
    if isinstance(chainlink_gate, dict)
    else 'CHAINLINK_RUNTIME_BLOCKED'
)
result = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': 'DEPLOY_ACCEPTED',
    'runtime_health_status': runtime_health_status,
    'deployed_sha': expected_sha,
    'smoke_started_at': smoke_started_at,
    'app_mode': 'PAPER',
    'live_trading_enabled': False,
    'live_auto_arm': False,
    'real_order_submission': False,
    'shadow_restarts_before': rb,
    'shadow_restarts_after': ra,
    'cycle_count_before': cb,
    'cycle_count_after': ca,
    'latest_cycle_before': tb,
    'latest_cycle_after': ta,
    'settlement_scan_count_before': settlement_before,
    'settlement_scan_count_after': settlement_after,
    'latest_settlement_scan_before': settlement_ts_before,
    'latest_settlement_scan_after': settlement_ts_after,
    'dashboard_endpoints': endpoints,
    'paper_summary': paper_summary,
    'shadow_status': shadow_status,
    'chainlink': collector.get('chainlink') if isinstance(collector, dict) else {},
    'chainlink_gate': chainlink_gate,
    'chainlink_gate_status': chainlink_gate_status,
    'binance_hourly': collector.get('binance_hourly') if isinstance(collector, dict) else {},
    'ptb_ready_if_present': ptb_ready,
}
Path(path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
print('DIRECTION_ENGINE_V3_DEPLOY_RESULT_BEGIN')
print(json.dumps(result, sort_keys=True))
print('DIRECTION_ENGINE_V3_DEPLOY_RESULT_END')
PY
}

main() {
  require_safe_environment
  checkout_exact_sha
  install_dependencies_if_needed
  validate_on_vps
  stop_project_runtime_units
  install_project_units
  start_shadow_and_wait_ready
  start_dashboard_and_report
  smoke_check
  log "DEPLOY_PAPER_ACCEPTED expected_sha=$EXPECTED_SHA"
}

main
