#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SHA="${1:?expected git SHA is required}"
PROJECT_DIR="${2:-/home/ubuntu/direction-engine-v3}"
PY="$PROJECT_DIR/.venv/bin/python"
DEPLOY_STATE_DIR="/var/lib/direction-engine-v3/deploy"
LEGACY_DEPLOY_STATE_DIR="$PROJECT_DIR/runtime/deploy"
DEPLOY_RESULT="$DEPLOY_STATE_DIR/github-paper-deploy-result.json"
STAGE="start"

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

dump_failure_context() {
  local exit_code="$1"
  log "DEPLOY_FAILED stage=$STAGE exit_code=$exit_code expected_sha=$EXPECTED_SHA"
  for unit in direction-engine-v3-shadow.service direction-engine-v3-dashboard.service; do
    systemctl --no-pager --full status "$unit" || true
    journalctl -u "$unit" --no-pager -n 80 || true
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
  if [ "$(run_ubuntu "git status --short")" != "" ]; then
    echo "Project working tree is dirty; refusing exact-SHA deploy" >&2
    run_ubuntu "git status --short"
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
  systemctl restart direction-engine-v3-dashboard.service
  systemctl restart direction-engine-v3-shadow.service
  systemctl restart direction-engine-v3-shadow-report.timer
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

smoke_check() {
  STAGE="fast-smoke"
  systemctl is-active --quiet direction-engine-v3-shadow.service
  systemctl is-active --quiet direction-engine-v3-dashboard.service
  local restarts_before restarts_after cycles_before cycles_after cycle_ts_before cycle_ts_after
  restarts_before="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
  cycles_before="$(cycle_count)"
  cycle_ts_before="$(latest_cycle_ts)"
  sleep 75
  systemctl is-active --quiet direction-engine-v3-shadow.service
  systemctl is-active --quiet direction-engine-v3-dashboard.service
  restarts_after="$(systemctl show -p NRestarts --value direction-engine-v3-shadow.service)"
  cycles_after="$(cycle_count)"
  cycle_ts_after="$(latest_cycle_ts)"
  if [ "$restarts_after" != "$restarts_before" ]; then
    echo "Shadow service restart count changed during smoke" >&2
    exit 20
  fi
  if [ "$cycles_after" -le "$cycles_before" ] || [ "$cycle_ts_after" = "$cycle_ts_before" ]; then
    echo "Shadow daemon did not advance a REAL_SHADOW_CYCLE during smoke" >&2
    exit 21
  fi
  curl -fsS http://127.0.0.1:8130/ >/tmp/direction-engine-v3-dashboard.html
  curl -fsS http://127.0.0.1:8130/api/dashboard >/tmp/direction-engine-v3-dashboard.json
  curl -fsS http://127.0.0.1:8130/api/directional/status >/tmp/direction-engine-v3-directional.json
  grep -q "PAPER / SHADOW" /tmp/direction-engine-v3-dashboard.html
  run_ubuntu_python <<'PY'
from direction_engine_v3.config import APP_MODE, LIVE_AUTO_ARM, LIVE_TRADING_ENABLED
if APP_MODE != 'PAPER' or LIVE_TRADING_ENABLED or LIVE_AUTO_ARM:
    raise SystemExit('PAPER/LIVE safety defaults violated')
PY
  write_result "$restarts_before" "$restarts_after" "$cycles_before" "$cycles_after" "$cycle_ts_before" "$cycle_ts_after"
}

write_result() {
  STAGE="write-result"
  local restarts_before="$1" restarts_after="$2" cycles_before="$3" cycles_after="$4"
  local cycle_ts_before="$5" cycle_ts_after="$6"
  "$PY" - "$DEPLOY_RESULT" "$EXPECTED_SHA" "$restarts_before" "$restarts_after" "$cycles_before" "$cycles_after" "$cycle_ts_before" "$cycle_ts_after" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, expected_sha, rb, ra, cb, ca, tb, ta = sys.argv[1:]

def read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        return {'read_error': type(exc).__name__}

directional = read_json('/tmp/direction-engine-v3-directional.json')
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
result = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': 'DEPLOY_PAPER_ACCEPTED',
    'deployed_sha': expected_sha,
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
    'chainlink': collector.get('chainlink') if isinstance(collector, dict) else {},
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
  install_project_units
  smoke_check
  log "DEPLOY_PAPER_ACCEPTED expected_sha=$EXPECTED_SHA"
}

main
