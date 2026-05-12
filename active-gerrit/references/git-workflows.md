# Local Git Workflows

Use this file when a user asks for local fetch, checkout, worktree, amend, or push-review flows, or when a task mixes Gerrit REST facts with local Git state.

These workflows are generic. Keep team policy, reviewer assignment rules, and release gates in `active-gerrit-workflow`.

## Global Rules

- Start with `repo-info` or `repo-status` before any Git command that could change the working tree or remote state.
- Prefer Gerrit REST as the source of truth for change number, project, branch, current patch set, and `RevisionInfo.ref`.
- Resolve the Gerrit remote before fetch or push: explicit `--remote` first, then `GERRIT_GIT_REMOTE`, then remotes matching `GERRIT_BASE_URL`, then `origin`.
- Reject dirty worktrees for `checkout-change` and review push flows unless the user explicitly chose a safer alternative such as `worktree-change` or enabled the documented override.
- Keep `Change-Id` stable for patch set updates. Treat missing or changed `Change-Id` as a warning or blocker, not a silent rewrite.
- Redact remote URL credentials, tokens, and push stderr before returning output.

## Verify Local Git Readiness

Collect:

```text
python scripts/git_cli.py git-doctor
python scripts/git_cli.py repo-info
python scripts/git_cli.py repo-config
```

Return Git version, repo root, current branch, upstream, remotes, identity config, and `commit-msg` hook status.

## Read Local Repository State

1. Use `repo-info` to resolve repo root, HEAD, current branch, upstream, ahead/behind, and remotes.
2. Use `repo-status` for machine-readable staged, unstaged, untracked, and conflict entries.
3. Use `repo-remotes` when remote selection or credential redaction matters.
4. Use `repo-config` when the task depends on `user.name`, `user.email`, branch merge config, or `commit-msg` hook presence.

## Read Local Diff And History

1. Use `repo-diff` for multi-file summaries.
2. Add `--staged` when the user cares about the index rather than the working tree.
3. Add `--base <rev>` when the diff should be relative to a specific commit or branch.
4. Add `--include-patch` only when the task truly needs patch text.
5. Use `repo-diff-file --path <file>` for single-file investigation.
6. Use `repo-log`, `repo-show`, or `repo-branches` when history or branch selection is part of the plan.

## Fetch A Gerrit Patch Set

1. Call `active-gerrit get-change --detail detail` to resolve project, branch, and revision metadata.
2. Prefer `RevisionInfo.ref` from Gerrit response.
3. Run `fetch-change --change <project~number> [--revision current|<patch-set>|<sha>]`.
4. Return the resolved patch set, ref, selected remote, fetched commit, and remote-selection warnings.

Fallback ref construction is only acceptable when Gerrit REST facts are unavailable and the user explicitly supplied change number plus patch set:

```text
refs/changes/<last-two>/<change-number>/<patch-set>
```

## Check Out A Gerrit Patch Set

1. Run `repo-status` first.
2. If the worktree is dirty, stop and recommend `worktree-change` unless the user explicitly wants dirty checkout behavior.
3. Run `checkout-change` to fetch and switch to `FETCH_HEAD`.
4. Default branch naming should be `review/<change>-<patchset>` unless the user overrides `--branch`.
5. Detached checkout is acceptable for pure read-only inspection.

Return the fetch facts, checkout mode, resulting branch, and current branch after checkout.

## Create An Isolated Worktree For Review

1. Use `worktree-change` when the current worktree is dirty or when the user wants multiple reviews checked out at once.
2. Resolve the patch set the same way as `fetch-change`.
3. Default the worktree path adjacent to the main repo unless the user provides `--path`.
4. Refuse non-empty target paths.

Return the same change facts as checkout plus `worktree.path`, `worktree.repo_root`, and `worktree.head`.

## Prepare A Patch Set Update

1. Use `repo-status` and `repo-diff` to summarize local changes.
2. Run `change-id-check` on `HEAD` or a message file.
3. Confirm the repo still points at the intended change and branch.
4. Use `commit-plan` before `commit-create` or `commit-amend`.
5. For patch set updates, prefer `commit-amend` and preserve the existing `Change-Id` unless the user explicitly wants a new review.

## Plan A Gerrit Review Push

1. Require a clean worktree.
2. Resolve target branch in this order: explicit `--branch`, `GERRIT_REVIEW_BRANCH`, current branch merge config, upstream short branch, current branch.
3. Read HEAD commit message and validate `Change-Id`.
4. Build `refs/for/<branch>` with optional `topic`, `reviewer`, `cc`, `hashtag`, `wip`, or `ready` options.
5. Use `push-review-plan` to return remote, branch source, target ref, refspec, hook status, and warnings.

## Execute A Gerrit Review Push

1. Start from the `push-review-plan` fields.
2. Default to `git push --dry-run --porcelain`.
3. Only execute a real push when the user explicitly confirmed and `--yes` is present.
4. Preserve redacted stdout and stderr so remote rejection diagnostics are still visible.
5. After a real push, refresh Gerrit change state through `active-gerrit` if the task needs the new patch set number.

## Mixed Gerrit And Git Workflows

### Local Review Preparation

```text
1. active-gerrit: get-change --detail full
2. git_cli: repo-info
3. git_cli: repo-status
4. git_cli: fetch-change
5. git_cli: checkout-change or worktree-change
6. local test/search/inspection
7. active-gerrit: review/comment/vote
```

### Fix And Upload A New Patch Set

```text
1. active-gerrit: get-change
2. git_cli: fetch-change + checkout-change/worktree-change
3. modify files locally
4. git_cli: repo-status + repo-diff
5. git_cli: commit-amend
6. git_cli: push-review-plan
7. git_cli: push-review --yes
8. active-gerrit: get-change --detail detail
```

### Create A New Review From Current Branch

```text
1. git_cli: repo-info + repo-status
2. git_cli: change-id-check
3. git_cli: push-review-plan
4. git_cli: push-review --yes
5. active-gerrit: query-changes --query <Change-Id>
```

## Environment Knobs

| Variable | Default | Purpose |
|---|---|---|
| `GIT_BIN` | `git` | Override Git executable path. |
| `GIT_TIMEOUT_SECONDS` | `60` | Default timeout for ordinary Git commands. |
| `GIT_FETCH_TIMEOUT_SECONDS` | `180` | Fetch timeout override. |
| `GIT_PUSH_TIMEOUT_SECONDS` | `300` | Push timeout override. |
| `GERRIT_GIT_REMOTE` | empty | Default Gerrit remote name. |
| `GERRIT_REVIEW_BRANCH` | empty | Default review target branch. |
| `GIT_ALLOW_DIRTY_CHECKOUT` | `false` | Allow dirty checkout when the caller explicitly accepts the risk. |