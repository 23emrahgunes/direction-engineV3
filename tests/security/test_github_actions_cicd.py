import json
from pathlib import Path

import yaml

ROOT = Path(".")
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-paper.yml"
DEPLOY_SCRIPT = ROOT / "deploy" / "aws" / "paper_deploy.sh"
TRUST_POLICY = ROOT / "deploy" / "aws" / "github-oidc-trust-policy.json"
PERMISSIONS_POLICY = ROOT / "deploy" / "aws" / "github-deploy-permissions.json"
OIDC_ROLE = "arn:aws:iam::605618941421:role/direction-engine-v3-github-deploy-role"


def test_github_workflows_are_valid_yaml() -> None:
    for path in (CI_WORKFLOW, DEPLOY_WORKFLOW):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        assert parsed["name"]
        assert parsed["jobs"]


def test_ci_workflow_runs_required_python_acceptance_commands() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python-version: \"3.12\"" in source
    assert "python -m compileall src tests" in source
    assert "python -m pytest -q" in source
    assert "python -m ruff check ." in source
    assert "python -m mypy src" in source
    assert "git diff --check" in source
    assert "bash -n deploy/aws/paper_deploy.sh" in source
    assert "aws-actions/configure-aws-credentials" not in source
    assert "aws ssm" not in source


def test_deploy_workflow_uses_oidc_ssm_exact_sha_and_concurrency() -> None:
    source = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" in source
    assert "aws-actions/configure-aws-credentials@v4" in source
    assert "role-to-assume: ${{ env.AWS_ROLE_ARN }}" in source
    assert OIDC_ROLE in source
    assert "AWS_ACCESS_KEY_ID" not in source
    assert "AWS_SECRET_ACCESS_KEY" not in source
    assert "aws ssm send-command" in source
    assert "AWS-RunShellScript" in source
    assert "github.event.workflow_run.head_sha" in source
    assert "github.sha" in source
    assert "direction-engine-v3-paper-deploy" in source
    assert "cancel-in-progress: false" in source
    assert "bash -n deploy/aws/paper_deploy.sh" in source
    assert "ssh " not in source.lower()


def test_deploy_script_is_fast_paper_only_and_does_not_wait_for_strategy_evidence() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "wait_for_shadow_cycle_and_settlement_progress" in source
    assert "deadline=$((SECONDS + 180))" in source
    assert "sleep 10" in source
    assert "--max-time 8" in source
    assert "PTB_READY_NOT_OBSERVED" not in source
    assert "BOUNDARY" not in source
    assert "24h" not in source.lower()
    assert "workflow_dispatch" not in source
    assert "run_ubuntu_python()" in source
    assert "run_ubuntu \"$PY - <<'PY'" not in source
    assert "LIVE_TRADING_ENABLED=true" not in source
    assert "LIVE_AUTO_ARM=true" not in source
    assert "real_order_submission=true" not in source
    assert "sign_order" not in source
    assert "submit_order" not in source
    assert "create-order" not in source.lower()
    assert "direction-engine-v3-shadow.service" in source
    assert "direction-engine-v3-dashboard.service" in source
    assert "systemctl restart" in source
    assert "wait_shadow_startup_ready" in source
    assert "SHADOW_STARTUP_READY" in source
    assert "CHAINLINK_RUNTIME_BLOCKED" in source
    assert "CHAINLINK_ACCEPTED" in source
    assert "WAITING_FOR_FRESH_PIPELINE_EVIDENCE" in source
    assert "chainlink_gate" in source
    assert 'chainlink_gate "$smoke_started_at"' in source
    assert "parse_success_count" in source
    assert "history_size" in source
    assert "last_message_at" in source
    assert "subscription_snapshot_count" in source
    assert "last_frame_class" in source
    assert "status': 'DEPLOY_ACCEPTED'" in source
    assert "runtime_health_status" in source
    assert "RUNTIME_HEALTH_OK" in source
    assert "RUNTIME_HEALTH_WARN" in source
    assert "RUNTIME_HEALTH_BLOCKED" in source


def test_deploy_chainlink_gate_requires_fresh_current_schema_evidence() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "smoke_started_at=\"$(date -u +%FT%TZ)\"" in source
    assert "observed_at >= smoke_started_at" in source
    assert "fresh_candidates" in source
    assert "missing_fresh_assets" in source
    assert "WAITING_FOR_FRESH_PIPELINE_EVIDENCE" in source
    assert "latest_observed_at" in source
    assert "pipeline_observed_at" in source
    assert "last_observed_at" in source
    assert "subscription_snapshot_count" in source
    assert "last_frame_class" in source
    assert "subscription_snapshot_count') or 0) < 1" in source
    assert "not state.get('last_frame_class')" in source
    assert "exit 22" not in source


def test_deploy_smoke_records_dashboard_endpoints_and_settlement_progress() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "probe_dashboard_endpoint()" in source
    assert 'probe_dashboard_endpoint "root" "http://127.0.0.1:8130/"' in source
    assert (
        'probe_dashboard_endpoint "paper-summary" '
        '"http://127.0.0.1:8130/api/paper/summary"'
    ) in source
    assert (
        'probe_dashboard_endpoint "directional" '
        '"http://127.0.0.1:8130/api/directional/status"'
    ) in source
    assert (
        'probe_dashboard_endpoint "shadow" '
        '"http://127.0.0.1:8130/api/shadow/status"'
    ) in source
    assert "settlement_scan_count()" in source
    assert "latest_settlement_scan_ts()" in source
    assert "PAPER_SETTLEMENT_SCAN" in source
    assert "Shadow daemon did not advance a PAPER_SETTLEMENT_SCAN during smoke" in source
    assert "exit 24" in source
    assert "'dashboard_endpoints': endpoints" in source
    assert "'paper_summary': paper_summary" in source
    assert "'shadow_status': shadow_status" in source
    assert "'settlement_scan_count_before': settlement_before" in source
    assert "'settlement_scan_count_after': settlement_after" in source
    assert "'shadow_main_pid': shadow_main_pid" in source
    assert "'shadow_invocation_id': shadow_invocation_id" in source
    assert "'orphan_shadow_daemon_count': int(orphan_shadow_daemon_count or 0)" in source
    assert "'orphan_shadow_daemon_cleaned': int(orphan_shadow_daemon_cleaned or 0)" in source
    assert "'orphan_shadow_daemon_killed': int(orphan_shadow_daemon_killed or 0)" in source


def test_deploy_starts_shadow_before_dashboard_and_report_smoke() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "stop_project_runtime_units" in source
    assert "start_shadow_and_wait_ready" in source
    assert "start_dashboard_and_report" in source
    assert "shadow_started_at=\"$(date -u +%FT%TZ)\"" in source
    assert "wait_shadow_startup_ready \"$shadow_started_at\"" in source
    assert "sqlite3.connect(db_path, timeout=1.0)" in source
    assert "FROM evidence_windows" in source
    assert "ORDER BY started_at DESC LIMIT 1" in source

    main_block = source[source.index("main() {") :]
    stop_units = main_block.index("stop_project_runtime_units")
    orphan_cleanup = main_block.index("cleanup_orphan_shadow_daemons")
    runtime_ownership = main_block.index("ensure_runtime_writable_by_service_user")
    assert stop_units < orphan_cleanup < runtime_ownership

    shadow_start = source.index("start_shadow_and_wait_ready")
    dashboard_start = source.index("start_dashboard_and_report")
    smoke = source.index("smoke_check")
    assert shadow_start < dashboard_start < smoke

    function_start = source.index("start_shadow_and_wait_ready()")
    function_end = source.index("start_dashboard_and_report()")
    shadow_function = source[function_start:function_end]
    assert "systemctl restart direction-engine-v3-shadow.service" in shadow_function
    assert "systemctl restart direction-engine-v3-dashboard.service" not in shadow_function


def test_deploy_cleans_only_verified_orphan_shadow_daemons() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "verified_orphan_shadow_pids()" in source
    assert "cleanup_orphan_shadow_daemons()" in source
    assert "direction_engine_v3.shadow.daemon" in source
    assert (
        'main_pid="$(systemctl show -p MainPID --value direction-engine-v3-shadow.service'
        in source
    )
    assert '[ "$pid" = "${main_pid:-0}" ]' in source
    assert 'cmdline="$(tr \'\\0\' \' \' < "$proc/cmdline"' in source
    assert 'cwd="$(readlink -f "$proc/cwd"' in source
    assert 'exe="$(readlink -f "$proc/exe"' in source
    assert '[ "$cwd" = "$project_real" ]' in source
    assert '[[ "$cmdline" == *"$PROJECT_DIR"* ]]' in source
    assert '[[ "$cmdline" == *"$PY"* ]]' in source
    assert '[[ "$exe" == "$project_real/.venv/bin/"* ]]' in source
    assert "WARN refusing non-project shadow-like process" in source
    assert "ORPHAN_SHADOW_DAEMON_FOUND" in source
    assert "kill -TERM" in source
    assert "deadline=$((SECONDS + 10))" in source
    assert "ORPHAN_SHADOW_DAEMON_TERM_TIMEOUT" in source
    assert "kill -KILL" in source
    assert "ORPHAN_SHADOW_DAEMON_CLEANED" in source
    assert "NO_ORPHAN_SHADOW_DAEMON" in source
    assert "exit 25" in source
    assert "pkill" not in source
    assert "kill -9" not in source


def test_deploy_failure_diagnostics_include_current_journal_and_preserve_stage() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "DEPLOY_STARTED_AT=\"$(date -u +%FT%TZ)\"" in source
    assert "DEPLOY_FAILED stage=$STAGE exit_code=$exit_code" in source
    assert "InvocationID" in source
    assert "NRestarts" in source
    assert "journalctl -u \"$unit\" --since \"$DEPLOY_STARTED_AT\"" in source
    assert "trap 'dump_failure_context \"$?\"' ERR" in source
    assert "dump_shadow_smoke_context()" in source
    assert "SHADOW_CURRENT_INVOCATION_JOURNAL" in source
    assert "journalctl _SYSTEMD_INVOCATION_ID=\"$invocation_id\"" in source
    assert "SHADOW_EVENT_STORAGE_BUSY" in source
    assert "dump_shadow_smoke_context \"REAL_SHADOW_CYCLE_NOT_ADVANCED\"" in source
    assert "dump_shadow_smoke_context \"PAPER_SETTLEMENT_SCAN_NOT_ADVANCED\"" in source


def test_deploy_precheck_cannot_self_dirty_project_worktree() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'DEPLOY_STATE_DIR="/var/lib/direction-engine-v3/deploy"' in source
    assert 'LEGACY_DEPLOY_STATE_DIR="$PROJECT_DIR/runtime/deploy"' in source
    assert 'PAPER_ARCHIVE_DIR="$PROJECT_DIR/runtime/archive"' in source
    assert 'DEPLOY_RESULT="$DEPLOY_STATE_DIR/github-paper-deploy-result.json"' in source
    assert '\nDEPLOY_STATE_DIR="$PROJECT_DIR/runtime/deploy"' not in source
    assert 'mkdir -p "$DEPLOY_STATE_DIR"' in source
    assert 'rm -rf -- "$legacy_real"' in source
    assert "deploy_git_status()" in source
    assert "git status --short -- . ':(exclude)runtime/archive'" in source
    status_check = source.index('git status --short')
    state_creation = source.index('mkdir -p "$DEPLOY_STATE_DIR"')
    assert status_check < state_creation
    assert 'dirty_status="$(deploy_git_status)"' in source
    assert 'if [ "$dirty_status" != "" ]; then' in source
    assert 'exit 11' in source


def test_legacy_cleanup_is_canonical_and_protects_runtime_evidence() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'runtime_real="$(realpath -e "$project_real/runtime")"' in source
    assert 'expected_legacy="$runtime_real/deploy"' in source
    assert 'legacy_real="$expected_legacy"' not in source
    assert '[ "$legacy_real" = "/" ]' in source
    assert '[ "$legacy_real" = "$project_real" ]' in source
    assert '[ "$legacy_real" = "$runtime_real" ]' in source
    assert 'rm -rf -- "$PROJECT_DIR/runtime"' not in source
    assert 'rm -rf -- "$project_real/runtime"' not in source
    assert 'rm -rf -- "$PAPER_ARCHIVE_DIR"' not in source
    assert 'rm -rf -- "$PROJECT_DIR/runtime/archive"' not in source
    assert 'rm -rf -- "$project_real/runtime/archive"' not in source
    assert "git status --short -- . ':(exclude)runtime/archive'" in source


def test_runtime_archive_is_gitignored_but_not_deleted_by_deploy() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "runtime/archive/*" in gitignore
    assert "!runtime/archive/.gitkeep" in gitignore
    assert (ROOT / "runtime" / "archive" / ".gitkeep").exists()
    assert 'PAPER_ARCHIVE_DIR="$PROJECT_DIR/runtime/archive"' in source
    assert "git status --short -- . ':(exclude)runtime/archive'" in source
    assert 'rm -rf -- "$legacy_real"' in source
    assert "runtime/deploy" in source
    assert 'rm -rf -- "$PAPER_ARCHIVE_DIR"' not in source
    assert 'reset_paper_run.py' not in source


def test_deploy_repairs_runtime_sqlite_ownership_without_resetting_paper() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "ensure_runtime_writable_by_service_user()" in source
    assert 'install -d -o ubuntu -g ubuntu "$runtime_dir" "$data_dir"' in source
    assert "find \"$data_dir\" -maxdepth 1 -type f" in source
    assert "-name '*.sqlite3'" in source
    assert "-name '*.sqlite3-journal'" in source
    assert "-name '*.sqlite3-wal'" in source
    assert "-name '*.sqlite3-shm'" in source
    assert "-exec chown ubuntu:ubuntu {} +" in source
    main_block = source[source.index("main() {") : source.index("  start_shadow_and_wait_ready")]
    assert main_block.index("stop_project_runtime_units") < main_block.index(
        "ensure_runtime_writable_by_service_user"
    )
    assert main_block.index("ensure_runtime_writable_by_service_user") < main_block.index(
        "install_project_units"
    )
    assert 'reset_paper_run.py' not in source


def test_iam_trust_policy_restricts_repo_branch_and_audience() -> None:
    policy = json.loads(TRUST_POLICY.read_text(encoding="utf-8"))
    statement = policy["Statement"][0]
    condition = statement["Condition"]["StringEquals"]

    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert condition["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subject = condition["token.actions.githubusercontent.com:sub"]
    assert subject == (
        "repo:23emrahgunes@168855296/direction-engineV3@1371242858:"
        "ref:refs/heads/main"
    )
    assert subject.endswith(":ref:refs/heads/main")


def test_iam_permission_policy_is_ssm_only_and_excludes_dangerous_actions() -> None:
    policy = json.loads(PERMISSIONS_POLICY.read_text(encoding="utf-8"))
    encoded = json.dumps(policy)
    allowed_actions = {
        action
        for statement in policy["Statement"]
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert allowed_actions == {
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:DescribeInstanceInformation",
    }
    assert "i-0c0e730834569177e" in encoded
    assert "AWS-RunShellScript" in encoded
    forbidden = (
        "AdministratorAccess",
        "iam:",
        "ec2:Authorize",
        "ec2:Revoke",
        "ec2:RunInstances",
        "secretsmanager:",
        "kms:Decrypt",
        "wallet",
    )
    assert not any(item.lower() in encoded.lower() for item in forbidden)


def test_oidc_setup_doc_warns_against_static_or_root_credentials() -> None:
    source = (ROOT / "docs" / "GITHUB-AWS-OIDC-SETUP.md").read_text(encoding="utf-8")

    assert "Do not use or store root access keys in GitHub" in source
    assert "AdministratorAccess" in source
    assert "direction-engine-v3-github-deploy-role" in source
    assert "aws iam create-role" in source
