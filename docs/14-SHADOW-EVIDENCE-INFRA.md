# V3.15 Shadow Evidence Infrastructure

V3.15 infrastructure records PAPER/SHADOW evidence and promotion-gate reports without
adding LIVE behavior.

- Runtime data: `runtime/data/shadow_evidence.sqlite3`
- Runtime reports: `runtime/reports/shadow_summary.json` and `runtime/reports/shadow_summary.md`
- Service: `direction-engine-v3-shadow.service`
- Timer: `direction-engine-v3-shadow.timer`

The first V3.15 AWS identity check returned `arn:aws:iam::605618941421:root`, so the
reports record `AWS_ROOT_PROFILE_SECURITY_DEBT`. The root profile is used only for the
previously approved project-scoped SSM operation, not for IAM, VPC, security group, route,
or other AWS control-plane changes.

The infrastructure status after deployment is expected to be:

`V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING`

The final `v3.15.0` tag is not allowed until the real burn-in and sample gates are
evaluated honestly.
