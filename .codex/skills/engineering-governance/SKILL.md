---
name: engineering-governance
description: Governs safe, bounded, evidence-driven code changes in direction-engineV3. Use for implementation, refactoring, bug fixing, migration, reviews, and any task that could alter trading behavior.
---

# Engineering Governance

Use this skill to keep changes small, testable, and auditable.

## Workflow

Before changing code:

1. State the requested behavior in one sentence.
2. Locate the owning module and its current tests.
3. Inspect callers and downstream contracts.
4. Decide the smallest coherent change.
5. Identify trading-safety invariants that must remain true.

During implementation:

- Change the owner, not random callers.
- Do not create parallel duplicate implementations unless an explicit experiment requires isolation.
- Preserve working behavior outside scope.
- Never hide a failed invariant with a default value.
- Never replace unavailable production data with mock data.
- Never weaken a test simply to make CI green.
- Avoid broad rewrites when a focused patch is sufficient.

## Anti-loop rule

If the same proposed fix fails twice:

1. stop patching;
2. gather new evidence;
3. inspect actual runtime/state/data flow;
4. revise the hypothesis;
5. only then make another change.

Do not repeatedly edit the same code without new evidence.

## Verification

Use the strongest checks available in the repository, normally:

- Python compile/syntax check;
- targeted unit tests;
- affected integration tests;
- relevant regression/replay tests;
- type/lint checks if configured;
- `git diff --check`;
- manual inspection of `git diff`.

Never report a command as passed unless it was run and passed.

## Completion report

Report:

- files changed;
- behavior changed;
- tests run;
- exact failures or unverified areas;
- whether PAPER/LIVE defaults changed;
- remaining risk.

A task with a blocking failed test is not complete.
