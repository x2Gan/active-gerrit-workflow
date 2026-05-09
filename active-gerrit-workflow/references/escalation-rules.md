# Escalation Rules

This file defines conservative escalation defaults for workflow reports. Treat these as placeholders until project-specific contacts and severities are documented.

## When To Escalate

Escalate instead of silently proceeding when any of the following is true:

- A blocking submit requirement, mergeability issue, or hidden submitted-together change exists.
- Auth, permission, secret, release, migration, or deploy paths changed.
- Reviewer ownership is unclear for a risky change.
- The change is WIP, private in an unexpected context, or has unresolved blocking comments.
- A production-impacting or release-branch decision lacks explicit policy evidence.

## Default Escalation Order

If no repository-specific rule exists, use this fallback order:

1. Change owner.
2. Visible reviewers on the change.
3. Release owner or on-call reviewer for release-style branches.
4. Repository administrator or submit right holder when permissions or labels are unclear.

If any step has no documented contact, report the gap and set `needs_human_decision: true`.

## Blocker Categories

Use these categories in workflow reports when escalation is needed:

- `submit_blocker`: Gerrit or policy blocks submit now.
- `review_blocker`: review findings or unresolved discussion block progress.
- `policy_gap`: a business rule, owner, or reviewer mapping is missing.
- `visibility_gap`: private or non-visible companion changes hide required evidence.
- `release_risk`: release-specific approval or rollback evidence is missing.

## Placeholder Contact Rules

Until team-specific contacts are documented, do not invent assignees for:

- Branch owners.
- File-path owners.
- Release approvers.
- Emergency or security responders.

Instead, suggest likely candidates and keep the final decision human-owned.

## Suggested Workflow Output

When escalation is required, include:

- The blocker category.
- What evidence is missing or what risk was detected.
- Who should confirm next, if known.
- A next action that keeps the workflow read-only unless the user explicitly asks for a write action.
