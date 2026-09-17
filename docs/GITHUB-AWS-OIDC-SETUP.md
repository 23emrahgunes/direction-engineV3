# GitHub Actions AWS OIDC setup for direction-engineV3 PAPER deploy

This is a one-time AWS account setup gate. Codex must not create or approve this
role automatically. After this setup, ordinary PAPER/SHADOW deployments should
run from GitHub Actions without local PowerShell AWS login, local AWS root
profiles, static access keys, or manual SSM send-command steps.

## Safety scope

- Repository: `23emrahgunes/direction-engineV3`
- Branch: `main`
- AWS account: `605618941421`
- Region: `eu-north-1`
- Target instance: `i-0c0e730834569177e`
- Target project path: `/home/ubuntu/direction-engine-v3`
- Role name: `direction-engine-v3-github-deploy-role`

The role is for PAPER/SHADOW deployment only. Do not attach
`AdministratorAccess`, IAM mutation permissions, EC2/VPC mutation permissions,
Secrets Manager permissions, wallet credentials, or any Polymarket private-key
permission.

## AWS Console setup

1. Open AWS IAM in account `605618941421`.
2. Confirm the OIDC provider exists:
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
3. If it does not exist, create an IAM identity provider with those values.
4. Create role `direction-engine-v3-github-deploy-role`.
5. Use the trust policy from:
   - `deploy/aws/github-oidc-trust-policy.json`
6. Create and attach an inline or customer-managed permissions policy from:
   - `deploy/aws/github-deploy-permissions.json`
7. Confirm the trust policy restricts `sub` exactly to:
   - `repo:23emrahgunes/direction-engineV3:ref:refs/heads/main`

## AWS CLI setup alternative

Run these commands only from an explicitly authorized AWS administrator context.
Do not use or store root access keys in GitHub.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

If the provider already exists, skip the previous command.

```bash
aws iam create-role \
  --role-name direction-engine-v3-github-deploy-role \
  --assume-role-policy-document file://deploy/aws/github-oidc-trust-policy.json

aws iam put-role-policy \
  --role-name direction-engine-v3-github-deploy-role \
  --policy-name direction-engine-v3-paper-ssm-deploy \
  --policy-document file://deploy/aws/github-deploy-permissions.json
```

## Validation after setup

1. Push to `main` and wait for the `CI` workflow to pass.
2. Run `Deploy PAPER` with `workflow_dispatch`, or let the successful main CI
   trigger it.
3. Confirm GitHub Actions logs show:
   - OIDC role assumption through `aws-actions/configure-aws-credentials`
   - an SSM command ID
   - deployed SHA equal to the GitHub SHA
   - `DEPLOY_PAPER_ACCEPTED`
4. Confirm the workflow does not wait for PTB boundaries, PAPER trades,
   settlement, or burn-in evidence.

## Rollback and failure handling

The deploy script refuses to continue when the VPS project worktree is dirty.
It prints the SSM command ID, failing stage, stdout/stderr, systemd status, and
recent sanitized journal tails in GitHub Actions logs. It restarts only these
project-owned units:

- `direction-engine-v3-shadow.service`
- `direction-engine-v3-dashboard.service`
- `direction-engine-v3-shadow-report.timer`

No unrelated services, security groups, VPC resources, IAM policies, wallets, or
LIVE trading controls are changed by the workflow.
