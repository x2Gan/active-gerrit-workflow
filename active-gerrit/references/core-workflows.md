# Core Gerrit Workflows

Use this file when a user asks for a common Gerrit operation and the correct sequence matters. These workflows are generic and must not include team-specific policy.

## Global Rules

- Resolve change input to `<project>~<number>` early.
- Use `current` by default only for reads; resolve to a patch set number before caching or writing.
- Query summary first, then fetch detail only when needed.
- Before any write, refresh the target change and current patch set.
- For high-risk actions, produce a dry-run plan and require explicit confirmation.

## Verify Connection

Collect:

```text
GET /config/server/version
GET /accounts/self/detail
GET /accounts/self/capabilities
```

Return version, current account, auth type, redacted credential checks, and warnings for optional tooling.

## My Review Queue

1. Query `reviewer:self -owner:self status:open` with `CURRENT_REVISION`, `DETAILED_ACCOUNTS`, `DETAILED_LABELS`, and `SUBMIT_REQUIREMENTS`.
2. Normalize each result to `ChangeSummary`.
3. Sort by user request; default to oldest `updated` first for review queues.
4. Mark WIP, private, unresolved comments, missing labels, and changes where the current user is in attention set if available.
5. Fetch detail only for changes the user wants to inspect.

## Change Review Context

1. Fetch change detail with `CURRENT_REVISION`, `CURRENT_COMMIT`, `CURRENT_FILES`, `DETAILED_ACCOUNTS`, `DETAILED_LABELS`, `SUBMIT_REQUIREMENTS`, `MESSAGES`, and `REVIEWER_UPDATES`.
2. Fetch published comments and current user's drafts when reviewing comment state.
3. List files for the target revision.
4. Read diffs only for requested files or files selected by risk and size.
5. Return a compact summary plus evidence pointers; keep raw payload out by default.

## Read File Diff

1. Resolve change and revision.
2. URL encode file path.
3. Request:

```text
GET /changes/{change-id}/revisions/{revision-id}/files/{file-id}/diff
```

4. Include `context`, `intraline`, `base`, and `ignore-whitespace` only when requested.
5. Normalize to `FileDiff` while preserving Gerrit `content`, `meta_a`, `meta_b`, and `diff_header`.

## Post Review, Vote, Or Comment

1. Refresh change detail and verify the target revision is still current unless the user explicitly selected an older patch set.
2. Validate labels against available label ranges if detailed labels are available.
3. Validate inline comments: file exists, line number exists on the selected side, and unresolved state is explicit.
4. Build `ReviewInput`.
5. Dry-run should return `ReviewPlan` without POST.
6. Execute with:

```text
POST /changes/{change-id}/revisions/{revision-id}/review
```

7. Return posted message, label changes, comment count, notify mode, and Gerrit response summary.

## Add Or Remove Reviewers

Add reviewer:

```text
POST /changes/{change-id}/reviewers
```

Remove reviewer:

```text
DELETE /changes/{change-id}/reviewers/{account-id}
```

Before removing, display resolved reviewer identity, project, branch, and change status.

## Submit Precheck

Submit planning must not rely on cached state.

1. Fetch detail with `DETAILED_LABELS`, `SUBMIT_REQUIREMENTS`, `CURRENT_REVISION`, `CURRENT_ACTIONS`, and `SUBMITTABLE`.
2. Fetch `GET /changes/{change-id}/revisions/current/mergeable`.
3. Fetch `GET /changes/{change-id}/submitted_together`.
4. Check `status == NEW`.
5. Check submit requirements and available submit action.
6. Show submitted-together changes and non-visible changes if returned.
7. Return a submit plan with blockers, warnings, and the exact POST that would run.
8. Only after explicit confirmation execute `POST /changes/{change-id}/submit`.

## Change State Actions

High-risk actions: submit, abandon, restore, rebase, move, revert, revert submission, cherry-pick.

Required plan fields: action, change, project, branch, owner, current revision, current status, reason, notify mode, expected effect, and whether `--yes` was supplied.

Default behavior is dry-run. Execute only when the user has clearly confirmed.

## Change Edit

1. Fetch existing edit state.
2. If an edit already exists, show owner and base patch set.
3. Only modify explicitly requested files.
4. For file writes, read the current file or diff first.
5. Before publishing, show changed files and commit message.
6. Publish with `POST /changes/{change-id}/edit:publish`.

## Project Config Through Review

Prefer review-producing endpoints over direct mutation:

```text
PUT /projects/{project-name}/config:review
PUT /projects/{project-name}/access:review
POST /projects/{project-name}/labels:review
POST /projects/{project-name}/submit_requirements:review
```

Direct access, label, submit requirement, cache, index, plugin, branch delete, and tag delete operations are administrator workflows and should default to dry-run.
