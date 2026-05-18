---
name: active-gerrit
description: "Use this skill for Gerrit Code Review REST API and local Git tasks: connection checks, querying changes, reading diffs and comments, posting reviews or votes, managing reviewers, submit or rebase actions, project and branch lookup, fetching/checking out Gerrit patch sets, preparing review pushes, or as the fallback Gerrit capability for workflow skills."
---

# Active Gerrit

## Purpose

`active-gerrit` is the foundation skill for Gerrit Code Review REST API and local Git work. It owns authentication, request shaping, XSSI JSON cleanup, URL encoding, error interpretation, stable result schemas, low-level Gerrit workflows, and safe local Git wrappers for Gerrit patch set workflows.

Keep this skill generic. Do not add team-specific review rules, release policy, approval logic, or business risk scoring here; those belong in `active-gerrit-workflow`.

## Default Workflow

1. Invoke the installed launcher (`active-gerrit ...`) for normal use. Start with `active-gerrit doctor` or `active-gerrit whoami`, especially before authenticated or write operations.
2. Query changes with lightweight summary fields first, then fetch detail only when the task needs labels, submit requirements, revisions, comments, or messages.
3. Resolve the target change and revision before reading files, diffs, comments, or submitting review input. If the user provides a Gerrit Web URL or surrounding prose that contains one, pass it directly to `--change`; the CLI normalizes it before REST path encoding.
4. For writes, refresh the current patch set before posting comments, votes, reviewer changes, submit, rebase, abandon, restore, WIP, or ready actions.
5. For local Git work, start with the documented Git wrapper command once available; use `repo-status` before checkout, commit, cherry-pick, or push workflows.
6. Prefer `active-gerrit` commands over hand-built REST or Git commands once an operation has a wrapper.

## Runtime Configuration

- `active-gerrit` launchers source `~/.config/active-gerrit-workflow/env` before running Python.
- `scripts/*.py` files are implementation entry points for development and tests. Do not call `python scripts/gerrit_cli.py ...` as the normal Agent path.
- If `scripts/gerrit_cli.py` is run directly, it will try to load `$ACTIVE_GERRIT_WORKFLOW_ENV_FILE` or `~/.config/active-gerrit-workflow/env` only when required Gerrit environment values are missing. Already-set environment variables take priority over file values.
- On credential/config failures, run `active-gerrit doctor --json` before asking the user to reconfigure credentials.

## References

- Read `references/gerrit-rest-api-3.11.2.md` when endpoint paths, query parameters, payload fields, or response entities are unclear.
- Read `references/core-workflows.md` when the user asks for a common Gerrit operation and the step order matters.
- Read `references/result-schemas.md` when consuming script output, handling cache keys, or mapping errors.
- Read `references/git-workflows.md` before local fetch, checkout, worktree, amend, or push-review flows, or when mixing Gerrit REST facts with local Git commands.

## Safety Rules

- Never print Gerrit passwords, tokens, cookies, or `Authorization` headers.
- Prefer environment variables for credentials; do not put secrets in command arguments.
- Treat submit, rebase, abandon, restore, project access changes, plugin operations, cache/index operations, and administrator actions as high risk.
- Treat local `commit`, `amend`, `cherry-pick`, checkout over a dirty worktree, and review push as high risk; check local status first and require an explicit plan before changing local or remote state.
- Do not run destructive Git commands such as `reset --hard`, `clean -fd`, branch deletion, remote URL mutation, or force push unless a future wrapper explicitly supports the operation with dry-run and confirmation safeguards.
- Before high-risk writes, show the target change, project, branch, revision, intended action, and reason, then require explicit confirmation unless the user already gave it.
- On failures, return diagnostic but redacted context: HTTP status, Gerrit message, endpoint category, and likely next step.

## Resource Layout

- `scripts/gerrit_cli.py` holds stable Python CLI wrappers for Gerrit REST operations.
- `scripts/git_cli.py` holds stable Python CLI wrappers for local Git operations used by Gerrit workflows.
- `scripts/git_runner.py`, `scripts/git_schemas.py`, and `scripts/git_gerrit.py` hold local Git subprocess, output schema, and Gerrit ref helpers.
- `references/` holds detailed API, workflow, and schema material loaded only when needed, including local Git workflow guidance.
- `agents/openai.yaml` holds UI metadata for the skill.
