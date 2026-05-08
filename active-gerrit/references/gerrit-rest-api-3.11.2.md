# Gerrit REST API 3.11.2 Reference

This is the compact API reference for `active-gerrit`. Use the larger `doc/Gerrit REST API.md` only when this file lacks a field or endpoint needed for implementation.

## Request Rules

- Base URL is `GERRIT_BASE_URL`, without a trailing slash.
- Authenticated REST calls use `/a/` plus HTTP Basic Auth in the first implementation stage.
- Required headers for JSON calls: `Accept: application/json` and `Content-Type: application/json; charset=UTF-8` when a body is present.
- Gerrit JSON often starts with the XSSI prefix `)]}'`; remove the first line before JSON parsing.
- Support repeated query parameters, especially multiple `o=` and multiple `q=`.
- URL encode every path parameter: project, branch, file path, account, group, tag, label, and submit requirement names.
- Prefer `pp=0` or `Accept: application/json` for compact JSON.
- Support `trace=<id>` or `X-Gerrit-Trace`; support `X-Gerrit-Deadline` when the CLI exposes deadlines.

## ID Forms

| ID | Preferred form | Notes |
|---|---|---|
| account | `self`, numeric `_account_id`, username, or email | Persist numeric `_account_id` when possible. |
| change | `<project>~<number>` | Avoid ambiguous raw change numbers after initial resolution. |
| revision | `current`, patch set number, or commit SHA | Resolve `current` before caching revision-specific data. |
| file | URL-encoded repository path | Special files: `/COMMIT_MSG`, `/PATCHSET_LEVEL`. |

## Status Handling

| Status | Meaning | Agent hint |
|---|---|---|
| `200` | Read or update succeeded. | Parse body if present. |
| `201` | Created. | Return created resource summary. |
| `204` | Succeeded with no body. | Return `data: null`. |
| `400` | Invalid query, body, or argument. | Show validation hint. |
| `401` | Missing or invalid authentication. | Check username and HTTP password. |
| `403` | Authenticated but not permitted. | Mention likely project permission or capability. |
| `404` | Missing or not visible. | Say resource may not exist or may be hidden. |
| `409` | State conflict. | Refresh change state and explain current blocker. |
| `412` | Precondition failed. | Re-read requirements or revision state. |
| `422` | Semantic input failure. | Show Gerrit message and payload category. |

## Change Query

Endpoint:

```http
GET /changes/?q=<query>&n=<limit>&S=<start>&o=<option>&o=<option>
```

Common queries:

```text
reviewer:self -owner:self status:open
owner:self status:open
project:{project} status:open
project:{project} branch:{branch} status:open
status:open is:submittable
status:open -is:wip
change:{number}
{Change-Id}
message:"text"
after:2026-05-01 before:2026-05-08
```

Useful `o=` options:

| Option | Use when |
|---|---|
| `CURRENT_REVISION` | Need current patch set id. |
| `CURRENT_COMMIT` | Need commit subject/message/parents. |
| `CURRENT_FILES` | Need file list and insert/delete counts. |
| `DETAILED_ACCOUNTS` | Need stable account fields. |
| `DETAILED_LABELS` | Need label values and voting ranges. |
| `SUBMIT_REQUIREMENTS` | Need submit readiness. |
| `MESSAGES` | Need change messages. |
| `REVIEWER_UPDATES` | Need reviewer history. |
| `CURRENT_ACTIONS` | Need user-available actions. |
| `SUBMITTABLE` | Need quick submit flag. |
| `DOWNLOAD_COMMANDS` | Need fetch commands; pair with revision options. |

## High-Use Endpoints

Paths below omit `/a/`; the client adds it for authenticated calls.

| Command area | Method | Path |
|---|---|---|
| version | `GET` | `/config/server/version` |
| whoami | `GET` | `/accounts/self/detail` |
| capabilities | `GET` | `/accounts/self/capabilities` |
| account query | `GET` | `/accounts/?q={query}` |
| change query | `GET` | `/changes/?q={query}` |
| change summary | `GET` | `/changes/{change-id}` |
| change detail | `GET` | `/changes/{change-id}/detail` |
| reviewers | `GET` | `/changes/{change-id}/reviewers/` |
| add reviewer | `POST` | `/changes/{change-id}/reviewers` |
| comments | `GET` | `/changes/{change-id}/comments` |
| drafts | `GET` | `/changes/{change-id}/drafts` |
| messages | `GET` | `/changes/{change-id}/messages` |
| files | `GET` | `/changes/{change-id}/revisions/{revision-id}/files/` |
| file content | `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/content` |
| file diff | `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/diff` |
| review | `POST` | `/changes/{change-id}/revisions/{revision-id}/review` |
| mergeable | `GET` | `/changes/{change-id}/revisions/{revision-id}/mergeable` |
| submitted together | `GET` | `/changes/{change-id}/submitted_together` |
| submit | `POST` | `/changes/{change-id}/submit` |
| abandon | `POST` | `/changes/{change-id}/abandon` |
| restore | `POST` | `/changes/{change-id}/restore` |
| rebase | `POST` | `/changes/{change-id}/rebase` |
| WIP | `POST` | `/changes/{change-id}/wip` |
| ready | `POST` | `/changes/{change-id}/ready` |
| edit file | `PUT` | `/changes/{change-id}/edit/{file-id}` |
| publish edit | `POST` | `/changes/{change-id}/edit:publish` |
| list projects | `GET` | `/projects/` |
| get project | `GET` | `/projects/{project-name}` |
| branches | `GET` | `/projects/{project-name}/branches/` |
| tags | `GET` | `/projects/{project-name}/tags/` |
| project config | `GET` | `/projects/{project-name}/config` |
| project config review | `PUT` | `/projects/{project-name}/config:review` |
| project access review | `PUT` | `/projects/{project-name}/access:review` |
| labels | `GET` | `/projects/{project-name}/labels/` |
| labels review | `POST` | `/projects/{project-name}/labels:review` |
| submit requirements | `GET` | `/projects/{project-name}/submit_requirements` |
| submit requirements review | `POST` | `/projects/{project-name}/submit_requirements:review` |

## Request Body Templates

Reviewer:

```json
{
  "reviewer": "alice@example.com",
  "state": "REVIEWER",
  "confirmed": true,
  "notify": "OWNER_REVIEWERS"
}
```

Review, vote, and comments:

```json
{
  "message": "Reviewed by agent.",
  "tag": "autogenerated:active-gerrit",
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
  },
  "drafts": "KEEP",
  "notify": "OWNER_REVIEWERS"
}
```

Submit:

```json
{
  "notify": "ALL"
}
```

Abandon or restore:

```json
{
  "message": "Reason visible to reviewers.",
  "notify": "OWNER_REVIEWERS"
}
```

Rebase:

```json
{
  "base": "myProject~4247",
  "allow_conflicts": false
}
```

WIP or ready:

```json
{
  "message": "Reason visible to reviewers."
}
```

Change edit publish:

```json
{
  "notify": "OWNER_REVIEWERS"
}
```

## Key Entities

`ChangeInfo` high-use fields: `id`, `_number`, `project`, `branch`, `topic`, `change_id`, `subject`, `status`, `created`, `updated`, `submitted`, `owner`, `labels`, `submit_requirements`, `current_revision`, `revisions`, `messages`, `reviewer_updates`, `insertions`, `deletions`, `unresolved_comment_count`, `mergeable`, `submittable`.

`RevisionInfo` high-use fields: `_number`, `created`, `uploader`, `ref`, `fetch`, `commit`, `files`, `actions`.

`FileInfo` high-use fields: `status`, `old_path`, `lines_inserted`, `lines_deleted`, `size_delta`, `size`, `old_mode`, `new_mode`.

`DiffInfo` high-use fields: `meta_a`, `meta_b`, `change_type`, `intraline_status`, `diff_header`, `content`, `web_links`.

`CommentInfo` high-use fields: `id`, `path`, `side`, `line`, `range`, `message`, `updated`, `author`, `unresolved`, `in_reply_to`.

## Implementation Pitfalls

- Do not request every `o=` option by default; large Gerrit instances become slow quickly.
- JSON bodies may silently ignore unknown fields; validate local payload keys.
- Text responses can still include the XSSI prefix.
- File content endpoints may return base64 or `text/plain`; branch by content type and endpoint semantics.
- `robotcomments` is deprecated in Gerrit 3.11.2; prefer normal comments with an autogenerated tag.
- Write operations must pass through the safety rules in `SKILL.md`.
