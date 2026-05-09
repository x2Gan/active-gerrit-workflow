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
  "topic": "feature-x"
}
```

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
  "size": 4096
}
```

FileDiff:

```json
{
  "change": "myProject~4247",
  "revision": "3",
  "base": "2",
  "file": "src/main/App.java",
  "change_type": "MODIFIED",
  "meta_a": {},
  "meta_b": {},
  "content": [],
  "diff_header": [],
  "warnings": []
}
```

ReviewPlan:

```json
{
  "change": "myProject~4247",
  "revision": "current",
  "resolved_revision": "3",
  "message": "Reviewed by agent.",
  "labels": {
    "Code-Review": 1
  },
  "comments_count": 2,
  "files": [
    "src/main/App.java"
  ],
  "notify": "OWNER_REVIEWERS",
  "dry_run": true
}
```

OperationResult:

```json
{
  "operation": "set-ready",
  "change": "myProject~4247",
  "before": {
    "status": "NEW",
    "work_in_progress": true
  },
  "after": {
    "status": "NEW",
    "work_in_progress": false
  },
  "updated_refs": [],
  "notify": "OWNER_REVIEWERS"
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
    "get-change",
    "get-mergeable",
    "submitted-together"
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
