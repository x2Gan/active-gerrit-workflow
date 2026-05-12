# Result Schemas

All `active-gerrit` scripts should print one JSON object to stdout. Do not print secrets, authorization headers, cookies, or raw request headers.

## Envelope

Success:

```json
{
  "ok": true,
  "command": "get-change",
  "source": "gerrit",
  "data": {},
  "warnings": [],
  "meta": {
    "gerrit_base_url": "https://gerrit.example.com",
    "api_version": "3.11.2",
    "fetched_at": "2026-05-08T10:00:00+08:00",
    "cache": "miss"
  }
}
```

Failure:

```json
{
  "ok": false,
  "command": "submit",
  "source": "gerrit",
  "data": null,
  "warnings": [],
  "error": {
    "type": "GerritConflict",
    "status": 409,
    "message": "Change is not ready to submit",
    "hint": "Refresh submit requirements and check missing labels."
  },
  "meta": {
    "fetched_at": "2026-05-08T10:00:00+08:00"
  }
}
```

## Error Types

| Type | Typical status | Meaning |
|---|---|---|
| `ConfigError` | none | Missing env var or invalid local config. |
| `GerritAuthError` | `401` | Authentication failed. |
| `GerritPermissionError` | `403` | Authenticated but not allowed. |
| `GerritNotFound` | `404` | Resource missing or hidden by permissions. |
| `GerritConflict` | `409` | Current Gerrit state blocks the action. |
| `GerritPreconditionFailed` | `412` | Required precondition missing. |
| `ValidationError` | `400` or none | Local argument or payload validation failed. |
| `TransportError` | none | Network, TLS, proxy, timeout, or DNS failure. |
| `ParseError` | none | Response could not be parsed as expected. |
| `GitConfigError` | none | Missing local repo, remote, hook, or Git config. |
| `GitCommandError` | none | Underlying Git command failed or timed out. |
| `GitConflict` | none | Git state conflict such as cherry-pick or merge issues. |
| `GitAuthError` | none | Fetch or push authentication failed. |
| `GitRemoteError` | none | Remote ref missing, remote unreachable, or push rejected. |
| `GitValidationError` | none | Local Git input or refspec validation failed. |

## Common Shapes

Account:

```json
{
  "account_id": 1000001,
  "name": "Alice",
  "email": "alice@example.com",
  "username": "alice"
}
```

ChangeSummary:

```json
{
  "id": "myProject~4247",
  "triplet_id": "myProject~master~Iabc",
  "number": 4247,
  "project": "myProject",
  "branch": "master",
  "change_id": "Iabc",
  "subject": "Fix bug",
  "status": "NEW",
  "owner": {
    "account_id": 1000001,
    "name": "Alice",
    "email": "alice@example.com",
    "username": "alice"
  },
  "updated": "2026-05-08 10:00:00.000000000",
  "current_revision": "abc123",
  "current_patch_set": 3,
  "labels": {},
  "submit_requirements": [],
  "unresolved_comment_count": 2,
  "hashtags": [],
  "topic": "feature-x",
  "is_private": false,
  "work_in_progress": false,
  "reviewed": false
}
```

`reviewed` is populated from Gerrit's `REVIEWED` query option and indicates whether the current user has already responded after the latest owner update. Workflow consumers use `is_private`, `work_in_progress`, `unresolved_comment_count`, and `reviewed` together for review-queue triage.

ChangeDetail:

```json
{
  "summary": {},
  "revisions": [
    {
      "revision": "abc123",
      "patch_set": 3,
      "created": "2026-05-08 10:00:00.000000000",
      "uploader": {},
      "ref": "refs/changes/47/4247/3",
      "files_count": 12,
      "files": [],
      "commit": {},
      "actions": {},
      "fetch": {}
    }
  ],
  "reviewers": {
    "REVIEWER": [],
    "CC": [],
    "REMOVED": []
  },
  "messages": [],
  "reviewer_updates": [],
  "actions": {},
  "raw": null
}
```

`get-change --detail files|full` populates `revisions[].files` and `revisions[].commit` when Gerrit returns `CURRENT_FILES` and `CURRENT_COMMIT`. `raw` is `null` unless `--include-raw` is supplied.

FileSummary:

```json
{
  "file": "src/main/App.java",
  "status": "M",
  "old_path": null,
  "lines_inserted": 10,
  "lines_deleted": 2,
  "size_delta": 120,
  "size": 4096,
  "old_mode": null,
  "new_mode": null
}
```

FileDiff:

```json
{
  "change": "myProject~4247",
  "revision": "3",
  "requested_revision": "current",
  "revision_sha": "abc123",
  "patch_set": 3,
  "base": "2",
  "file": "src/main/App.java",
  "change_type": "MODIFIED",
  "meta_a": {},
  "meta_b": {},
  "content": [],
  "diff_header": [],
  "intraline_status": "OK",
  "web_links": [],
  "warnings": []
}
```

FileContent:

```json
{
  "change": "myProject~4247",
  "revision": "3",
  "requested_revision": "current",
  "revision_sha": "abc123",
  "patch_set": 3,
  "file": "/COMMIT_MSG",
  "content": "Rml4IGJ1Zw==",
  "content_type": "text/plain; charset=UTF-8",
  "encoding": "base64"
}
```

CommentList:

```json
{
  "change": "myProject~4247",
  "kind": "published",
  "comments_by_file": {
    "src/main/App.java": [
      {
        "id": "c1",
        "path": "src/main/App.java",
        "side": "REVISION",
        "line": 42,
        "range": {},
        "message": "Please rename this variable.",
        "updated": "2026-05-08 09:30:00.000000000",
        "author": {},
        "unresolved": true,
        "in_reply_to": null,
        "patch_set": 3,
        "commit_id": "abc123",
        "tag": null
      }
    ]
  },
  "files": [
    {
      "file": "src/main/App.java",
      "comments": [],
      "count": 1,
      "unresolved_count": 1
    }
  ],
  "total_count": 1,
  "unresolved_count": 1
}
```

`list-comments` uses `kind: "published"`; `list-drafts` uses `kind: "draft"`.

MessageList:

```json
{
  "change": "myProject~4247",
  "messages": [
    {
      "id": "m1",
      "date": "2026-05-08 09:01:00.000000000",
      "author": {},
      "real_author": {},
      "message": "Patch Set 3: Uploaded patch set 3.",
      "tag": "autogenerated:upload",
      "revision_number": 3
    }
  ],
  "total_count": 1
}
```

ReviewerList:

```json
{
  "change": "myProject~4247",
  "reviewers": {
    "REVIEWER": [],
    "CC": [],
    "REMOVED": []
  },
  "counts": {
    "REVIEWER": 0,
    "CC": 0,
    "REMOVED": 0
  },
  "total_count": 0
}
```

ReviewPlan:

```json
{
  "change": "myProject~4247",
  "revision": "current",
  "resolved_revision": "3",
  "revision_sha": "abc123",
  "patch_set": 3,
  "message": "Reviewed by agent.",
  "labels": {
    "Code-Review": 1
  },
  "comments_count": 2,
  "files": [
    "src/main/App.java"
  ],
  "notify": "OWNER_REVIEWERS",
  "tag": "autogenerated:active-gerrit",
  "dry_run": true,
  "payload": {
    "message": "Reviewed by agent.",
    "tag": "autogenerated:active-gerrit",
    "notify": "OWNER_REVIEWERS",
    "labels": {
      "Code-Review": 1
    },
    "comments": {
      "src/main/App.java": [
        {
          "line": 42,
          "message": "Consider extracting this branch into a helper.",
          "unresolved": true
        }
      ]
    }
  }
}
```

ReviewResult:

```json
{
  "posted": true,
  "status": 200,
  "change": "myProject~4247",
  "revision": "current",
  "resolved_revision": "3",
  "revision_sha": "abc123",
  "patch_set": 3,
  "message": "Reviewed by agent.",
  "labels": {
    "Code-Review": 1
  },
  "comments_count": 1,
  "files": [
    "src/main/App.java"
  ],
  "notify": "OWNER_REVIEWERS",
  "tag": "autogenerated:active-gerrit",
  "response": {},
  "payload": {}
}
```

ReviewerOperationPlan:

```json
{
  "operation": "delete-vote",
  "change": "myProject~4247",
  "change_summary": {
    "id": "myProject~4247",
    "number": 4247,
    "project": "myProject",
    "branch": "master",
    "subject": "Fix bug",
    "status": "NEW",
    "owner": {
      "account_id": 1000001,
      "username": "alice",
      "email": "alice@example.com",
      "name": "Alice"
    },
    "current_patch_set": 3
  },
  "reviewer_input": "1000002",
  "reviewer": {
    "account_id": 1000002,
    "username": "bob",
    "email": "bob@example.com",
    "name": "Bob"
  },
  "state": "REVIEWER",
  "label": "Code-Review",
  "value": 2,
  "notify": null,
  "confirmed": false,
  "dry_run": true,
  "requires_confirmation": true,
  "payload": null
}
```

ReviewerOperationResult:

```json
{
  "operation": "add-reviewer",
  "change": "myProject~4247",
  "change_summary": {
    "id": "myProject~4247",
    "number": 4247,
    "project": "myProject",
    "branch": "master",
    "subject": "Fix bug",
    "status": "NEW",
    "owner": {
      "account_id": 1000001,
      "username": "alice",
      "email": "alice@example.com",
      "name": "Alice"
    },
    "current_patch_set": 3
  },
  "reviewer_input": "carol@example.com",
  "reviewer": {
    "account_id": 1000003,
    "username": "carol",
    "email": "carol@example.com",
    "name": "Carol"
  },
  "state": "CC",
  "label": null,
  "value": null,
  "notify": "OWNER_REVIEWERS",
  "confirmed": true,
  "requires_confirmation": false,
  "payload": {
    "reviewer": "carol@example.com",
    "state": "CC",
    "notify": "OWNER_REVIEWERS",
    "confirmed": true
  },
  "executed": true,
  "status": 200,
  "response": {},
  "added_reviewers": [],
  "added_ccs": [
    {
      "account_id": 1000003,
      "username": "carol",
      "email": "carol@example.com",
      "name": "Carol"
    }
  ],
  "confirm_required": false,
  "error": null
}
```

OperationResult:

```json
{
  "operation": "set-ready",
  "change": "myProject~4247",
  "before": {
    "status": "NEW",
    "work_in_progress": true,
    "topic": "feature-x",
    "hashtags": [
      "feature-x"
    ],
    "attention_set": [],
    "attention_count": 0
  },
  "after": {
    "status": "NEW",
    "work_in_progress": false,
    "topic": "feature-x",
    "hashtags": [
      "feature-x"
    ],
    "attention_set": [
      {
        "account": {
          "account_id": 1000002,
          "username": "bob",
          "email": "bob@example.com",
          "name": "Bob"
        },
        "last_update": "2026-05-08 09:50:00.000000000",
        "reason": "Ready for review",
        "reason_account": {}
      }
    ],
    "attention_count": 1
  },
  "status": 200,
  "updated_refs": [],
  "notify": "OWNER_REVIEWERS",
  "message": "Ready for review now.",
  "payload": {
    "ready": true,
    "message": "Ready for review now.",
    "notify": "OWNER_REVIEWERS",
    "tag": "autogenerated:active-gerrit"
  },
  "response": {}
}
```

WorkflowReport:

```json
{
  "workflow": "pre-submit-check",
  "ok": true,
  "target": {
    "change": "myProject~4247",
    "project": "myProject",
    "branch": "master"
  },
  "decision": {
    "status": "blocked",
    "summary": "Submit requirements are not satisfied.",
    "needs_human_decision": false
  },
  "checks": [
    {
      "name": "submit_requirements",
      "status": "failed",
      "evidence": [
        "Code-Review is missing"
      ]
    }
  ],
  "used_active_gerrit_commands": [
    "submit",
    "get-change",
    "list-files"
  ],
  "next_actions": [
    "Ask a reviewer for Code-Review +2."
  ],
  "meta": {
    "fetched_at": "2026-05-08T10:00:00+08:00",
    "policy_version": "review-policies@local"
  }
}
```

## Git Shapes

Git commands use the same envelope, but set `source: "git"` and include local runner metadata in `meta`.

Example:

```json
{
  "ok": true,
  "command": "repo-status",
  "source": "git",
  "data": {},
  "warnings": [],
  "meta": {
    "repo": "/path/to/repo",
    "git_bin": "git",
    "timeout_seconds": 60,
    "fetched_at": "2026-05-11T10:00:00+08:00"
  }
}
```

GitRemote:

```json
{
  "name": "origin",
  "fetch_url": "https://<redacted>@gerrit.example.com/project.git",
  "push_url": "https://<redacted>@gerrit.example.com/project.git"
}
```

GitRepoInfo:

```json
{
  "repo_root": "/path/to/repo",
  "git_dir": "/path/to/repo/.git",
  "is_inside_work_tree": true,
  "current_branch": "feature-x",
  "upstream": "origin/master",
  "ahead": 1,
  "behind": 0,
  "detached": false,
  "unborn": false,
  "upstream_gone": false,
  "head": "abc123def456",
  "head_short": "abc123d",
  "stash_count": 0,
  "remotes": [
    {
      "name": "origin",
      "fetch_url": "https://<redacted>@gerrit.example.com/project.git",
      "push_url": "https://<redacted>@gerrit.example.com/project.git"
    }
  ]
}
```

Used by `repo-info`; `warnings` may note missing upstream, detached HEAD, or unborn branches.

GitStatusEntry:

```json
{
  "code": "MM",
  "index_status": "M",
  "worktree_status": "M",
  "path": "src/main/App.java",
  "old_path": null,
  "conflict": false
}
```

GitStatus:

```json
{
  "repo_root": "/path/to/repo",
  "git_dir": "/path/to/repo/.git",
  "branch": "feature-x",
  "upstream": "origin/master",
  "ahead": 1,
  "behind": 0,
  "detached": false,
  "unborn": false,
  "upstream_gone": false,
  "is_clean": false,
  "staged": [
    {
      "code": "A ",
      "index_status": "A",
      "worktree_status": " ",
      "path": "staged.txt",
      "old_path": null,
      "conflict": false
    }
  ],
  "unstaged": [
    {
      "code": " M",
      "index_status": " ",
      "worktree_status": "M",
      "path": "tracked.txt",
      "old_path": null,
      "conflict": false
    }
  ],
  "untracked": [
    {
      "code": "??",
      "index_status": "?",
      "worktree_status": "?",
      "path": "untracked.txt",
      "old_path": null,
      "conflict": false
    }
  ],
  "conflicts": [],
  "ignored": [],
  "entries": [],
  "stash_count": 0
}
```

Used by `repo-status`; `entries` is the full parsed `git status --porcelain=v1 --branch -z` list, while `staged`, `unstaged`, `untracked`, and `conflicts` are filtered views.

GitDiffFile:

```json
{
  "path": "src/main/App.java",
  "old_path": null,
  "status": "M",
  "status_label": "modified",
  "raw_status": "M",
  "similarity": null,
  "insertions": 10,
  "deletions": 2,
  "binary": false
}
```

GitDiffSummary:

```json
{
  "repo_root": "/path/to/repo",
  "base": "HEAD",
  "target": "working-tree",
  "staged": false,
  "files": [
    {
      "path": "src/main/App.java",
      "old_path": null,
      "status": "M",
      "status_label": "modified",
      "raw_status": "M",
      "similarity": null,
      "insertions": 10,
      "deletions": 2,
      "binary": false
    }
  ],
  "stat": {
    "files_changed": 1,
    "insertions": 10,
    "deletions": 2,
    "binary_files": 0,
    "renamed_files": 0,
    "copied_files": 0,
    "deleted_files": 0
  },
  "patch": null,
  "patch_truncated": false,
  "requested_base": null,
  "stat_only": true,
  "include_patch": false
}
```

Used by `repo-diff`; `repo-diff-file` adds `path` for the requested file and still returns `files` for the parsed result set.

GitChangeFetch:

```json
{
  "repo_root": "/path/to/repo",
  "change": "demo~4247",
  "change_number": 4247,
  "requested_revision": "current",
  "resolved_revision": "3",
  "patch_set": 3,
  "ref": "refs/changes/47/4247/3",
  "ref_source": "revision_ref",
  "project": "demo",
  "branch": "master",
  "remote": "origin",
  "remote_reason": "explicit_arg",
  "remote_warnings": [],
  "fetch": {
    "stdout": "",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "timeout_seconds": 180.0
  },
  "fetched_commit": "abc123def456",
  "fetched_subject": "Fix bug"
}
```

Used by `fetch-change`; downstream checkout and worktree commands embed the same resolved change facts.

GitChangeCheckout:

```json
{
  "repo_root": "/path/to/repo",
  "change": "demo~4247",
  "change_number": 4247,
  "requested_revision": "current",
  "resolved_revision": "3",
  "patch_set": 3,
  "ref": "refs/changes/47/4247/3",
  "ref_source": "revision_ref",
  "project": "demo",
  "branch": "master",
  "remote": "origin",
  "remote_reason": "explicit_arg",
  "remote_warnings": [],
  "fetched_commit": "abc123def456",
  "fetched_subject": "Fix bug",
  "checkout_mode": "branch",
  "branch": "review/4247-3",
  "current_branch": "review/4247-3",
  "worktree": null
}
```

Used by `checkout-change`; `worktree-change` returns the same shape but populates `worktree.path`, `worktree.repo_root`, and `worktree.head`.

GitChangeId:

```json
{
  "source": "HEAD",
  "commit": "HEAD",
  "message_file": null,
  "present": true,
  "valid": true,
  "value": "Iabc1234",
  "all_values": [
    "Iabc1234"
  ],
  "valid_values": [
    "Iabc1234"
  ],
  "invalid_values": [],
  "count": 1,
  "summary": {
    "subject": "Fix bug",
    "body_line_count": 2,
    "line_count": 4,
    "has_body": true
  }
}
```

Used by `change-id-check`, `commit-plan`, `commit-amend`, and `push-review-plan`.

GitPushReviewPlan:

```json
{
  "repo_root": "/path/to/repo",
  "remote": "origin",
  "remote_reason": "matched_origin",
  "branch": "master",
  "branch_source": "branch_merge",
  "current_branch": "feature-x",
  "upstream": "origin/master",
  "head": "abc123def456",
  "head_short": "abc123d",
  "subject": "Fix bug",
  "change_id": {
    "value": "Iabc1234"
  },
  "options": {
    "topic": "feature/demo",
    "hashtag": [
      "release-1",
      "qa"
    ],
    "reviewer": [
      "alice@example.com"
    ],
    "cc": [
      "bob@example.com"
    ],
    "wip": true,
    "ready": false
  },
  "target_ref": "refs/for/master%topic=feature%2Fdemo,hashtag=release-1,hashtag=qa,reviewer=alice@example.com,cc=bob@example.com,wip",
  "refspec": "HEAD:refs/for/master%topic=feature%2Fdemo,hashtag=release-1,hashtag=qa,reviewer=alice@example.com,cc=bob@example.com,wip",
  "requires_clean_worktree": true,
  "hooks": {
    "commit_msg": {
      "ok": true,
      "required": false,
      "path": "/path/to/repo/.git/hooks/commit-msg",
      "configured_hooks_path": "/path/to/repo/.git/hooks",
      "executable": true,
      "hint": null
    }
  },
  "remote_branch": {
    "ref": "refs/heads/master",
    "exists": true
  },
  "mode": "plan",
  "dry_run": true,
  "push_executed": false
}
```

Used by `push-review-plan`. `push-review` returns the same planning fields plus `mode`, `dry_run`, `push_executed`, and a `push` object containing redacted `stdout`/`stderr` from `git push --porcelain`.

## Cache Rules

- Cache directory defaults to `.cache/gerrit/`.
- Never cache credentials, authorization headers, cookies, or full raw headers.
- Do not cache full source file content unless a command explicitly enables it.
- Resolve `current` revision to patch set number or SHA before long caching.
- Bypass cache before write operations and submit readiness checks.

Suggested cache TTL:

| Result | Key pattern | TTL |
|---|---|---|
| Version | `server/version` | 1 day |
| Server info | `server/info` | 1 hour |
| Current account | `accounts/self` | 1 hour |
| Account resolve | `accounts/resolve/<hash>` | 1 hour |
| Project list | `projects/list/<hash>` | 10 minutes |
| Branch list | `projects/<project>/branches` | 5 to 10 minutes |
| Change query | `changes/query/<hash>` | 30 to 60 seconds |
| Change detail | `changes/<change>/detail` | 15 to 30 seconds |
| Comments or messages | `changes/<change>/comments` | 30 seconds |
| Mergeable | `changes/<change>/mergeable` | 5 to 15 seconds |
| Revision diff | `revisions/<change>/<base>..<revision>/<file>/<hash>` | 7 days |

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Gerrit or validation failure represented in JSON. |
| `2` | CLI usage error. |
| `3` | Local configuration or dependency error. |
