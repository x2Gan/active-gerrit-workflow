---
name: active-gerrit
description: "Use this skill for Gerrit Code Review REST API tasks: connection checks, querying changes, reading diffs and comments, posting reviews or votes, managing reviewers, submit or rebase actions, project and branch lookup, or as the fallback Gerrit capability for workflow skills."
---

# Active Gerrit

## Purpose

`active-gerrit` is the foundation skill for Gerrit Code Review REST API work. It owns authentication, request shaping, XSSI JSON cleanup, URL encoding, error interpretation, stable result schemas, and low-level Gerrit workflows.

Keep this skill generic. Do not add team-specific review rules, release policy, approval logic, or business risk scoring here; those belong in `active-gerrit-workflow`.

## Default Workflow

1. Start with `doctor` or `whoami` once the CLI exists, especially before authenticated or write operations.
2. Query changes with lightweight summary fields first, then fetch detail only when the task needs labels, submit requirements, revisions, comments, or messages.
3. Resolve the target change and revision before reading files, diffs, comments, or submitting review input.
4. For writes, refresh the current patch set before posting comments, votes, reviewer changes, submit, rebase, abandon, restore, WIP, or ready actions.
5. Prefer scripts in `scripts/` over hand-built REST calls once an operation has a script wrapper.

## References

- Read `references/gerrit-rest-api-3.11.2.md` when endpoint paths, query parameters, payload fields, or response entities are unclear.
- Read `references/core-workflows.md` when the user asks for a common Gerrit operation and the step order matters.
- Read `references/result-schemas.md` when consuming script output, handling cache keys, or mapping errors.

## Safety Rules

- Never print Gerrit passwords, tokens, cookies, or `Authorization` headers.
- Prefer environment variables for credentials; do not put secrets in command arguments.
- Treat submit, rebase, abandon, restore, project access changes, plugin operations, cache/index operations, and administrator actions as high risk.
- Before high-risk writes, show the target change, project, branch, revision, intended action, and reason, then require explicit confirmation unless the user already gave it.
- On failures, return diagnostic but redacted context: HTTP status, Gerrit message, endpoint category, and likely next step.

## Resource Layout

- `scripts/` will hold stable Python CLI and client modules for Gerrit operations.
- `references/` will hold detailed API, workflow, and schema material loaded only when needed.
- `agents/openai.yaml` holds UI metadata for the skill.
