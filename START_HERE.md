# START HERE — direction-engineV3 Skill Pack

## 1. Copy/extract

Extract this pack directly into:

`C:\wamp64\www\direction-engineV3`

You should then have:

- `AGENTS.md`
- `.codex\skills\...`
- `SKILLS_INDEX.md`
- `scripts\verify-skills.ps1`
- `scripts\install-skills-user.ps1`

## 2. Verify layout

Open PowerShell in the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-skills.ps1
```

## 3. Start a NEW Codex session

Skill discovery is normally session-scoped/cached, so open a new session after adding or changing skills.

## 4. If repo-local skills do not appear

Some Codex environments/builds can differ in local-skill discovery. Install a copy into the user Codex skill directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-skills-user.ps1
```

Then start another new Codex session.

The script copies these project skills to:

`%USERPROFILE%\.codex\skills`

It does not delete unrelated existing skills.

## 5. First Codex prompt

Use:

```text
$engineering-governance $repo-architecture $testing-qa

We are bootstrapping direction-engineV3.
Read AGENTS.md first.
Do not implement trading strategy yet.

Create only the minimal V3 repository/package/test skeleton required for:
data → features → strategy → risk → execution → reconciliation.

Default mode must remain PAPER.
LIVE must remain disabled.
Run all bootstrap validation and report exact results.
```

After the clean skeleton passes, proceed to WhaleSignal migration in small phases.
