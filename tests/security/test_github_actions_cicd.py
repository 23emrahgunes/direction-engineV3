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
    assert "aws" not in source.lower()


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
    assert "ssh " not in source.lower()


def test_deploy_script_is_fast_paper_only_and_does_not_wait_for_strategy_evidence() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "sleep 75" in source
    assert "PTB_READY_NOT_OBSERVED" not in source
    assert "BOUNDARY" not in source
    assert "24h" not in source.lower()
    assert "workflow_dispatch" not in source
    assert "LIVE_TRADING_ENABLED=true" not in source
    assert "LIVE_AUTO_ARM=true" not in source
    assert "real_order_submission=true" not in source
    assert "sign_order" not in source
    assert "submit_order" not in source
    assert "create-order" not in source.lower()
    assert "direction-engine-v3-shadow.service" in source
    assert "direction-engine-v3-dashboard.service" in source
    assert "systemctl restart" in source


def test_iam_trust_policy_restricts_repo_branch_and_audience() -> None:
    policy = json.loads(TRUST_POLICY.read_text(encoding="utf-8"))
    statement = policy["Statement"][0]
    condition = statement["Condition"]["StringEquals"]

    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert condition["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    assert (
        condition["token.actions.githubusercontent.com:sub"]
        == "repo:23emrahgunes@168855296/direction-engineV3@1371242858:ref:refs/heads/main"
    )


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
