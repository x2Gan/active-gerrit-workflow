# Release Policies

This file captures conservative release-branch policy defaults. Treat every rule here as a placeholder until a repository or team-specific release policy is documented.

## Scope

Apply these defaults when a branch name or submit target suggests a release, stable, or hotfix workflow.

Branch markers that usually require release policy review:

- `release*`
- `stable*`
- `hotfix*`
- branch names or paths that include `/release/`, `/stable/`, or `/hotfix/`

## Default Release Submit Gate

A release-branch change should not receive an automatic `pass` unless all of the following are visible:

- Explicit owner identity and at least one reviewer are known.
- Submit requirements and required labels are satisfied.
- Test or CI evidence exists for the current patch set.
- Submitted-together scope is fully visible and expected.
- Rollback or mitigation thinking is available for risky changes.

If any of these are missing, return `needs_human_decision: true`.

## Higher-Risk Release Patterns

Treat these as higher risk and require explicit human review before submit:

- Auth, permission, secret, token, or access-control changes.
- Dependency, build, CI, deploy, or infra configuration changes.
- Migrations, data repair, feature flags, or compatibility toggles.
- Large diffs, generated artifacts, or unexpected submitted-together scope.
- Missing tests, unresolved comments, or very recent patch sets.

## Placeholder Ownership Rules

Until repository-specific ownership tables exist, assume these are unknown and require human confirmation:

- Release branch owner or approver roster.
- Required release manager or on-call reviewer.
- Branch-specific label or verification policy.
- Mandatory rollout, canary, or rollback checklist.

Workflow reports should name the missing policy area and set `needs_human_decision: true` instead of silently passing.

## Suggested Report Guidance

When release policy is incomplete, recommend actions like:

- Confirm release scope and companion changes.
- Ask the release owner or on-call reviewer for sign-off.
- Request CI or manual verification evidence for the current patch set.
- Confirm rollback steps for risky config or auth changes.
