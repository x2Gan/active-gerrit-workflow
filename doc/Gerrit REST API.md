# Gerrit Code Review 3.11.2 REST API 调研文档

> 目标：为后续封装 Gerrit Skill / Agent 工具提供一份可执行的 REST API 参考。
>
> 调研日期：2026-05-08
>
> 适配版本：Gerrit Code Review `3.11.2`

## 1. 官方资料入口

本文件基于 Gerrit 官方 3.11.2 版本文档整理，重点面向 Agent 自动化调用场景。官方文档按资源拆分，开发时建议把这里作为索引，把官方页面作为最终字段校验来源。

- REST API 总览：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api.html>
- REST API 开发者说明：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/dev-rest-api.html>
- Access API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-access.html>
- Accounts API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-accounts.html>
- Changes API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-changes.html>
- Config API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-config.html>
- Groups API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-groups.html>
- Plugins API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-plugins.html>
- Projects API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-projects.html>
- Documentation Search API：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api-documentation.html>
- Change 查询语法：<https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/user-search.html>

## 2. REST 调用基本规则

### 2.1 Base URL 与鉴权

Gerrit REST API 的路径挂在 Gerrit Web 根路径下：

```text
http(s)://<gerrit-host>/
```

匿名请求直接访问资源路径：

```text
GET https://gerrit.example.com/projects/
```

需要登录身份的请求使用 `/a/` 前缀，并配合 HTTP Basic Auth：

```text
GET https://gerrit.example.com/a/accounts/self/detail
Authorization: Basic <base64(username:http_password)>
```

实现 Skill 时建议要求用户配置：

```text
GERRIT_BASE_URL=https://gerrit.example.com
GERRIT_USERNAME=<username>
GERRIT_HTTP_PASSWORD=<gerrit-http-password>
```

注意事项：

- Gerrit 的 Basic Auth 密码通常是 Gerrit UI 中生成的 HTTP password，不一定是登录 UI 的密码。
- `/a/` 是鉴权入口，不是资源本身的一部分；内部 client 可统一拼成 `${baseUrl}/a${path}`。
- `self` / `me` 只在已鉴权请求中有意义。
- 可见性受权限过滤，用户看不到的账号、变更、项目或 ref 不会返回。
- 也可通过 URL query 中的 `access_token` 携带授权 cookie token；使用有效 `access_token` 时不需要 XSRF token。

### 2.2 CORS 与 XSRF

浏览器内调用 Gerrit 时会遇到 CORS/XSRF 规则：

- Cookie 鉴权的 mutation 请求（`POST`、`PUT`、`DELETE`）需要 `X-Gerrit-Auth` 请求头携带有效 XSRF token。
- 使用 `/a/` + HTTP Basic Auth 会绕开 XSRF token 要求，适合服务端 Agent。
- 使用 `access_token` query 参数也可授权请求。
- Gerrit 支持通过 `$m` 覆盖 HTTP method、`$ct` 覆盖实际 content type，以便某些浏览器场景用 `text/plain` 避免 CORS preflight。

Agent/CLI Skill 推荐只走服务端 HTTP Basic Auth，不走浏览器 cookie 鉴权。

### 2.3 请求与响应格式

常用请求头：

```http
Accept: application/json
Content-Type: application/json; charset=UTF-8
```

JSON 响应通常带 Gerrit 的 XSSI 防护前缀：

```text
)]}'
```

Skill 必须在 JSON parse 前去掉第一行前缀。推荐解析流程：

```javascript
function parseGerritJson(text) {
  const cleaned = text.startsWith(")]}'") ? text.split('\n').slice(1).join('\n') : text;
  return cleaned.trim() ? JSON.parse(cleaned) : null;
}
```

常见返回：

- `200 OK`：读取或更新成功。
- `201 Created` / 官方示例有时写 `201 OK`：创建成功。
- `204 No Content`：删除或无响应体操作成功。
- `400 Bad Request`：参数、JSON body 或业务输入错误。
- `401 Unauthorized`：未认证。
- `403 Forbidden`：已认证但权限不足。
- `404 Not Found`：资源不存在，或当前用户不可见。
- `409 Conflict`：状态冲突，如不能 submit、不能 rebase、分支冲突。
- `412 Precondition Failed`：前置条件不满足。
- `422 Unprocessable Entity`：实体语义无法处理。

性能建议：

- Gerrit 默认多数 JSON 响应是 pretty-print。
- 工具应通过 `Accept: application/json` 或 query `pp=0` 请求紧凑 JSON。
- 如 client 支持 gzip，可加 `Accept-Encoding: gzip`。

调试建议：

- 任意 REST endpoint 可加 `trace=<trace-id>` query，或请求头 `X-Gerrit-Trace: <trace-id>`，Gerrit 会在响应头返回 trace id，并在服务端日志中关联对应请求。
- 可用 `X-Gerrit-Deadline: 5m` 给单个请求设置 deadline，单位支持 `ms`、`sec`、`min` 等。
- 写请求如需知道被更新的 refs，可加 `X-Gerrit-UpdatedRef-Enabled: true`，响应头会返回一个或多个 `X-Gerrit-UpdatedRef`。

### 2.4 URL 编码

所有路径参数都应 URL encode。对 Agent 最容易踩坑的是：

- 项目名含 `/`：`platform/foo` 写成 `platform%2Ffoo`。
- 分支名含 `/`：`refs/heads/release/1.0` 写成 `refs%2Fheads%2Frelease%2F1.0`；部分接口允许省略 `refs/heads/`。
- 文件路径含 `/`：`src/main/App.java` 写成 `src%2Fmain%2FApp.java`。
- `+`、空格、`~`、`#` 等查询参数必须按 URL query 规则编码。

### 2.5 常用 ID 规则

#### `{account-id}`

可用形式：

- `self` 或 `me`
- numeric account id，如 `1000001`
- username
- email
- `Full Name <email@example.com>`
- `Full Name (1000001)`

Agent 实现建议：

- 用户明确说“我”时用 `self`。
- 工具内部持久化账号时优先用 numeric `_account_id`。
- 需要引用 inactive account 时只能稳定依赖 numeric id。

#### `{change-id}`

官方推荐优先使用：

```text
<project>~<changeNumber>
```

例如：

```text
myProject~4247
platform%2Ffoo~4247
```

也支持但不建议优先使用的形式：

- `<project>~<branch>~<Change-Id>`，如 `myProject~master~I8473...`
- 唯一的 `Change-Id`，如 `I8473...`
- 唯一的 change number，如 `4247`

原因：替代形式需要额外索引查找，可能有性能开销，也可能因索引或可见性导致误判。

#### `{revision-id}`

可用形式：

- `current`
- 完整 commit SHA-1
- 唯一的缩写 commit SHA-1，至少 4 位
- patch set number，如 `1`
- `0` 或 `edit` 表示 change edit

Agent 默认应使用 `current`，除非用户指定 patch set。

#### `{file-id}`

文件路径需要 URL encode。特殊文件：

- `/COMMIT_MSG`：commit message
- `/PATCHSET_LEVEL`：patchset-level comment

## 3. 查询参数与返回控制

### 3.1 Change 查询

核心接口：

```http
GET /changes/?q=<query>&n=<limit>&S=<start>&o=<option>&o=<option>
```

常用查询：

```text
status:open project:myProject branch:master
owner:self status:open
reviewer:self -owner:self status:open
is:open label:Code-Review>=1
change:4247
message:"fix typo"
after:2026-05-01 before:2026-05-08
```

分页：

- `n=<limit>`：最大返回数量。
- `S=<start>`：offset。
- 多个 `q` 可一次请求多个查询，响应是数组的数组。

### 3.2 ChangeInfo `o=` 选项

`/changes/` 和 `/changes/{id}` 可用 `o=` 控制返回字段。字段越多开销越大，Skill 应按任务选择最小集合。

常用组合：

```text
o=CURRENT_REVISION
o=CURRENT_COMMIT
o=CURRENT_FILES
o=DETAILED_ACCOUNTS
o=DETAILED_LABELS
o=SUBMIT_REQUIREMENTS
o=MESSAGES
o=REVIEWER_UPDATES
o=DOWNLOAD_COMMANDS
```

重要选项：

- `LABELS`：包含 label 信息。
- `DETAILED_LABELS`：包含 label、投票人、可投票范围等详细信息。
- `SUBMIT_REQUIREMENTS`：包含 submit requirement 评估结果。
- `CURRENT_REVISION`：包含当前 patch set 的 revision 信息。
- `ALL_REVISIONS`：包含所有 patch set。
- `DOWNLOAD_COMMANDS`：包含 fetch 命令；需配合 `CURRENT_REVISION` 或 `ALL_REVISIONS`。
- `CURRENT_COMMIT` / `ALL_COMMITS`：包含 commit header 和 message。
- `CURRENT_FILES` / `ALL_FILES`：包含文件列表和增删行概要。
- `DETAILED_ACCOUNTS`：账号引用中包含 `_account_id`、email、username。
- `MESSAGES`：包含 change messages。
- `CURRENT_ACTIONS` / `CHANGE_ACTIONS`：包含当前用户可执行动作。
- `SUBMITTABLE`：包含是否可提交。
- `CHECK`：包含潜在问题。
- `COMMIT_FOOTERS`：包含 Gerrit-specific footers。
- `TRACKING_IDS`：包含外部跟踪系统引用。
- `CUSTOM_KEYED_VALUES`：包含自定义 key-value。
- `STAR`：包含当前用户是否 starred。
- `PARENTS`：包含 revision parent 信息。

## 4. Agent / Skill 设计建议

建议把 Gerrit Skill 拆成以下工具层：

1. `gerrit_request(method, path, body?, query?)`
   - 统一处理 base URL、`/a/`、Basic Auth、JSON 序列化、XSSI 前缀、错误映射。
2. `query_changes(query, options?, limit?, start?)`
   - 默认 `o=CURRENT_REVISION,DETAILED_ACCOUNTS,DETAILED_LABELS,SUBMIT_REQUIREMENTS`。
3. `get_change(change_id, detail_level?)`
   - `summary` / `detail` / `files` / `full` 四档。
4. `review_change(change_id, revision_id='current', labels?, message?, comments?)`
   - 封装 `ReviewInput`。
5. `get_diff(change_id, file_path, revision_id='current', base?)`
   - 封装 file diff。
6. `add_reviewer(change_id, reviewer, state='REVIEWER')`
   - 封装 ReviewerInput。
7. `change_action(change_id, action, input?)`
   - submit、abandon、restore、rebase、move、wip、ready、private 等。
8. `project_*`
   - list/get/create project、branch、tag、access、labels、submit requirements。

错误处理建议：

- 对 `404` 同时提示“资源不存在或当前用户不可见”。
- 对 `409` 给出业务状态，如 submit 冲突、merge conflict、change closed。
- 对 `403` 提示需要的 Gerrit capability 或 project permission。
- 自动把 `Change-Id` / change number 转成推荐的 `<project>~<number>` 后缓存，减少后续索引查找。

## 5. 端点目录

以下端点来自 Gerrit 3.11.2 官方 REST 文档。表中的路径默认不含 `/a/`，需要认证时由 client 自动加 `/a/`。

### 5.1 `/access/`

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/access/?project={project-name}` | 查询多个项目的 access rights。 |

### 5.2 `/accounts/`

#### Account Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/accounts/` | 查询账号。 |
| `GET` | `/accounts/{account-id}` | 获取账号基础信息。 |
| `PUT` | `/accounts/{username}` | 创建账号。 |
| `DELETE` | `/accounts/{account-id}` | 删除账号；3.11.2 当前主要支持 self deletion。 |
| `GET` | `/accounts/{account-id}/detail` | 获取账号详细信息。 |
| `GET` | `/accounts/{account-id}/name` | 获取姓名。 |
| `PUT` | `/accounts/{account-id}/name` | 设置姓名。 |
| `DELETE` | `/accounts/{account-id}/name` | 删除姓名。 |
| `GET` | `/accounts/{account-id}/status` | 获取状态文本。 |
| `PUT` | `/accounts/{account-id}/status` | 设置状态文本。 |
| `GET` | `/accounts/{account-id}/username` | 获取 username。 |
| `PUT` | `/accounts/{account-id}/username` | 设置 username。 |
| `PUT` | `/accounts/{account-id}/displayname` | 设置 display name。 |
| `GET` | `/accounts/{account-id}/active` | 查询账号是否 active。 |
| `PUT` | `/accounts/{account-id}/active` | 激活账号。 |
| `DELETE` | `/accounts/{account-id}/active` | 停用账号。 |
| `PUT` | `/accounts/{account-id}/password.http` | 设置或生成 HTTP password。 |
| `DELETE` | `/accounts/{account-id}/password.http` | 删除 HTTP password。 |
| `GET` | `/accounts/{account-id}/oauthtoken` | 获取 OAuth access token。 |
| `GET` | `/accounts/{account-id}/state` | 获取账号状态。 |
| `GET` | `/accounts/{account-id}/emails` | 列出邮箱。 |
| `GET` | `/accounts/{account-id}/emails/{email-id}` | 获取邮箱信息。 |
| `PUT` | `/accounts/{account-id}/emails/{email-id}` | 创建邮箱。 |
| `DELETE` | `/accounts/{account-id}/emails/{email-id}` | 删除邮箱。 |
| `PUT` | `/accounts/{account-id}/emails/{email-id}/preferred` | 设置首选邮箱。 |
| `GET` | `/accounts/{account-id}/sshkeys` | 列出 SSH keys。 |
| `GET` | `/accounts/{account-id}/sshkeys/{ssh-key-id}` | 获取 SSH key。 |
| `POST` | `/accounts/{account-id}/sshkeys` | 添加 SSH key。 |
| `DELETE` | `/accounts/{account-id}/sshkeys/{ssh-key-id}` | 删除 SSH key。 |
| `GET` | `/accounts/{account-id}/gpgkeys` | 列出 GPG keys。 |
| `GET` | `/accounts/{account-id}/gpgkeys/{gpg-key-id}` | 获取 GPG key。 |
| `POST` | `/accounts/{account-id}/gpgkeys` | 添加或删除 GPG keys。 |
| `DELETE` | `/accounts/{account-id}/gpgkeys/{gpg-key-id}` | 删除 GPG key。 |
| `GET` | `/accounts/{account-id}/capabilities` | 列出账号 capabilities。 |
| `GET` | `/accounts/{account-id}/capabilities/{capability-id}` | 检查某个 capability。 |
| `GET` | `/accounts/{account-id}/groups/` | 列出账号所属组。 |
| `GET` | `/accounts/{account-id}/avatar` | 获取头像。 |
| `GET` | `/accounts/{account-id}/avatar.change.url` | 获取头像修改 URL。 |
| `GET` | `/accounts/{account-id}/preferences` | 获取用户偏好。 |
| `PUT` | `/accounts/{account-id}/preferences` | 设置用户偏好。 |
| `GET` | `/accounts/{account-id}/preferences.diff` | 获取 diff 偏好。 |
| `PUT` | `/accounts/{account-id}/preferences.diff` | 设置 diff 偏好。 |
| `GET` | `/accounts/{account-id}/preferences.edit` | 获取 edit 偏好。 |
| `PUT` | `/accounts/{account-id}/preferences.edit` | 设置 edit 偏好。 |
| `GET` | `/accounts/{account-id}/watched.projects` | 获取 watched projects。 |
| `POST` | `/accounts/{account-id}/watched.projects` | 添加或更新 watched projects。 |
| `POST` | `/accounts/{account-id}/watched.projects:delete` | 删除 watched projects。 |
| `GET` | `/accounts/{account-id}/external.ids` | 获取 external IDs。 |
| `POST` | `/accounts/{account-id}/external.ids:delete` | 删除 external IDs。 |
| `GET` | `/accounts/{account-id}/agreements` | 列出 contributor agreements。 |
| `POST` | `/accounts/{account-id}/drafts:delete` | 删除 draft comments。 |
| `PUT` | `/accounts/{account-id}/agreements` | 签署 contributor agreement。 |
| `POST` | `/accounts/{account-id}/index` | 重新索引账号。 |

#### Default Star Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/accounts/{account-id}/starred.changes` | 获取默认 starred changes。 |
| `PUT` | `/accounts/{account-id}/starred.changes/{change-id}` | 给 change 加默认 star。 |
| `DELETE` | `/accounts/{account-id}/starred.changes/{change-id}` | 移除默认 star。 |

### 5.3 `/changes/`

#### Change Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/changes/` | 创建 change。 |
| `GET` | `/changes/` | 查询 changes。 |
| `GET` | `/changes/{change-id}` | 获取 change。 |
| `GET` | `/changes/{change-id}/meta_diff?old=SHA-1&meta=SHA-1` | 查询 change meta 历史差异。 |
| `GET` | `/changes/{change-id}/detail` | 获取 change 详情。 |
| `POST` | `/changes/{change-id}/merge` | 基于 MergePatchSetInput 创建 merge patch set。 |
| `GET` | `/changes/{change-id}/message` | 获取当前 commit message。 |
| `PUT` | `/changes/{change-id}/message` | 修改 commit message 并创建新 patch set。 |
| `GET` | `/changes/{change-id}/topic` | 获取 topic。 |
| `PUT` | `/changes/{change-id}/topic` | 设置 topic。 |
| `DELETE` | `/changes/{change-id}/topic` | 删除 topic。 |
| `GET` | `/changes/{change-id}/pure_revert` | 检查是否为 pure revert。 |
| `POST` | `/changes/{change-id}/abandon` | abandon change。 |
| `POST` | `/changes/{change-id}/restore` | restore change。 |
| `POST` | `/changes/{change-id}/rebase` | rebase change。 |
| `POST` | `/changes/{change-id}/rebase:chain` | rebase 依赖链。 |
| `POST` | `/changes/{change-id}/move` | move change 到其他分支。 |
| `POST` | `/changes/{change-id}/revert` | revert change。 |
| `POST` | `/changes/{change-id}/revert_submission` | revert 整个 submission。 |
| `POST` | `/changes/{change-id}/submit` | submit change。 |
| `GET` | `/changes/{change-id}/submitted_together?o=NON_VISIBLE_CHANGES` | 查询会一起提交的 changes。 |
| `DELETE` | `/changes/{change-id}` | 删除 change。 |
| `POST` | `/changes/{change-id}/patch:apply` | 从 patch 创建 patch set。 |
| `GET` | `/changes/{change-id}/in` | 查询 change 已包含在哪些 branches/tags。 |
| `POST` | `/changes/{change-id}/index` | 重新索引 change。 |
| `GET` | `/changes/{change-id}/comments` | 列出所有 published comments。 |
| `GET` | `/changes/{change-id}/robotcomments` | 列出 robot comments；已 deprecated。 |
| `GET` | `/changes/{change-id}/drafts` | 列出当前用户 drafts。 |
| `GET` | `/changes/{change-id}/check` | 检查 change 一致性。 |
| `POST` | `/changes/{change-id}/check` | 检查并尝试修复 change。 |
| `POST` | `/changes/{change-id}/wip` | 标记 Work-In-Progress。 |
| `POST` | `/changes/{change-id}/ready` | 标记 Ready-For-Review。 |
| `POST` | `/changes/{change-id}/private` | 标记 private。 |
| `DELETE` | `/changes/{change-id}/private` | 取消 private。 |
| `GET` | `/changes/{change-id}/hashtags` | 获取 hashtags。 |
| `POST` | `/changes/{change-id}/hashtags` | 添加/删除 hashtags。 |
| `GET` | `/changes/{change-id}/custom_keyed_values` | 获取自定义 key-value。 |
| `POST` | `/changes/{change-id}/custom_keyed_values` | 添加/删除自定义 key-value。 |
| `GET` | `/changes/{change-id}/messages` | 列出 change messages。 |
| `GET` | `/changes/{change-id}/messages/{change-message-id}` | 获取单条 change message。 |
| `DELETE` | `/changes/{change-id}/messages/{change-message-id}` | 删除 change message。 |
| `POST` | `/changes/{change-id}/messages/{change-message-id}/delete` | 删除 change message 的 POST 替代形式。 |
| `POST` | `/changes/{change-id}/check.submit_requirement` | 测试 submit requirement。 |

#### Change Edit Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/changes/{change-id}/edit` | 获取当前用户 change edit 详情。 |
| `PUT` | `/changes/{change-id}/edit/{file-id}` | 修改 change edit 中的文件内容。 |
| `POST` | `/changes/{change-id}/edit` | 创建 edit、恢复文件或重命名文件。 |
| `PUT` | `/changes/{change-id}/edit:message` | 修改 edit 的 commit message。 |
| `DELETE` | `/changes/{change-id}/edit/{file-id}` | 删除 edit 中的文件。 |
| `PUT` | `/changes/{change-id}/edit:identity` | 修改 edit author 或 committer identity。 |
| `GET` | `/changes/{change-id}/edit/{file-id}` | 获取 edit 文件内容。 |
| `GET` | `/changes/{change-id}/edit/{file-id}/meta` | 获取 edit 文件元数据。 |
| `GET` | `/changes/{change-id}/edit:message` | 获取 edit 或当前 patch set 的 commit message。 |
| `POST` | `/changes/{change-id}/edit:publish` | 发布 edit 为 regular patch set。 |
| `POST` | `/changes/{change-id}/edit:rebase` | rebase change edit。 |
| `DELETE` | `/changes/{change-id}/edit` | 删除 change edit。 |

#### Reviewer Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/changes/{change-id}/reviewers/` | 列出 reviewers。 |
| `GET` | `/changes/{change-id}/suggest_reviewers?q=J&n=5` | 推荐 reviewers。 |
| `GET` | `/changes/{change-id}/suggest_reviewers?q=J&n=5&exclude-groups` | 推荐 reviewers，排除 groups。 |
| `GET` | `/changes/{change-id}/suggest_reviewers?q=J&reviewer-state=CC` | 推荐 CC。 |
| `GET` | `/changes/{change-id}/reviewers/{account-id}` | 获取 reviewer。 |
| `POST` | `/changes/{change-id}/reviewers` | 添加 reviewer 或 group。 |
| `DELETE` | `/changes/{change-id}/reviewers/{account-id}` | 删除 reviewer。 |
| `POST` | `/changes/{change-id}/reviewers/{account-id}/delete` | 删除 reviewer 的 POST 替代形式。 |
| `GET` | `/changes/{change-id}/reviewers/{account-id}/votes/` | 列出 reviewer votes。 |
| `DELETE` | `/changes/{change-id}/reviewers/{account-id}/votes/{label-id}` | 删除 reviewer vote。 |
| `POST` | `/changes/{change-id}/reviewers/{account-id}/votes/{label-id}/delete` | 删除 vote 的 POST 替代形式。 |

#### Revision Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/changes/{change-id}/revisions/{revision-id}/commit` | 获取 parsed commit。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/description` | 获取 patch set description。 |
| `PUT` | `/changes/{change-id}/revisions/{revision-id}/description` | 设置 patch set description。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/mergelist` | 获取 merge list。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/actions` | 获取 revision actions。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/review` | 获取 revision review 信息。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/related` | 获取相关 changes。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/review` | 设置 review、投票、评论、发布 drafts。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/rebase` | rebase revision。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/submit` | submit revision。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/patch` | 获取 patch。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/mergeable` | 获取 mergeable 信息。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/submit_type` | 获取 submit type。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/test.submit_type` | 测试 submit_type rule。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/test.submit_rule` | 测试 submit_rule。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/drafts/` | 列出 revision drafts。 |
| `PUT` | `/changes/{change-id}/revisions/{revision-id}/drafts` | 创建 draft comment。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/drafts/{draft-id}` | 获取 draft comment。 |
| `PUT` | `/changes/{change-id}/revisions/{revision-id}/drafts/{draft-id}` | 更新 draft comment。 |
| `DELETE` | `/changes/{change-id}/revisions/{revision-id}/drafts/{draft-id}` | 删除 draft comment。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/comments/` | 列出 revision published comments。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/comments/{comment-id}` | 获取 published comment。 |
| `DELETE` | `/changes/{change-id}/revisions/{revision-id}/comments/{comment-id}` | 删除 published comment。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/comments/{comment-id}/delete` | 删除 published comment 的 POST 替代形式。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/robotcomments/` | 列出 robot comments；已 deprecated。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/robotcomments/{comment-id}` | 获取 robot comment；已 deprecated。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/ported_comments` | 将其他 patch set comments 映射到当前 revision。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/ported_drafts` | 将 drafts 映射到当前 revision。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/fixes/{fix-id}/apply` | 应用 stored fix。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/fix:apply` | 应用 provided fix。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/files/` | 列出 revision files。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/content` | 获取文件内容。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/download` | 下载文件内容。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/diff` | 获取文件 diff。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/fixes/{fix-id}/preview` | 预览 stored fix。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/fix:preview` | 预览 provided fix。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/blame` | 获取 blame。 |
| `PUT` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/reviewed` | 标记文件已 reviewed。 |
| `DELETE` | `/changes/{change-id}/revisions/{revision-id}/files/{file-id}/reviewed` | 取消 reviewed 标记。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/cherrypick` | cherry-pick revision。 |

#### Revision Reviewer Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/changes/{change-id}/revisions/{revision-id}/reviewers/` | 列出 revision reviewers。 |
| `GET` | `/changes/{change-id}/revisions/{revision-id}/reviewers/{account-id}/votes/` | 列出 revision votes。 |
| `DELETE` | `/changes/{change-id}/revisions/{revision-id}/reviewers/{account-id}/votes/{label-id}` | 删除 revision vote。 |
| `POST` | `/changes/{change-id}/revisions/{revision-id}/reviewers/{account-id}/votes/{label-id}/delete` | 删除 revision vote 的 POST 替代形式。 |

#### Attention Set Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/changes/{change-id}/attention` | 获取 attention set。 |
| `POST` | `/changes/{change-id}/attention` | 添加用户到 attention set。 |
| `DELETE` | `/changes/{change-id}/attention/{account-id}` | 从 attention set 移除用户。 |
| `POST` | `/changes/{change-id}/attention/{account-id}/delete` | 移除用户的 POST 替代形式。 |

### 5.4 `/config/`

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/config/server/version` | 获取 Gerrit 版本。 |
| `GET` | `/config/server/info` | 获取 server info。 |
| `POST` | `/config/server/deactivate.stale.accounts` | 停用 stale accounts。 |
| `POST` | `/config/server/check.consistency` | 执行一致性检查。 |
| `POST` | `/config/server/reload` | reload config。 |
| `PUT` | `/config/server/email.confirm` | 确认邮箱。 |
| `GET` | `/config/server/caches/` | 列出 caches。 |
| `POST` | `/config/server/caches/` | 批量 cache operations。 |
| `GET` | `/config/server/caches/{cache-name}` | 获取 cache 信息。 |
| `POST` | `/config/server/caches/{cache-name}/flush` | flush cache。 |
| `GET` | `/config/server/summary` | 获取 server summary。 |
| `GET` | `/config/server/capabilities` | 列出全局 capabilities。 |
| `GET` | `/config/server/experiments` | 列出 experiments。 |
| `GET` | `/config/server/tasks/` | 列出 background tasks。 |
| `GET` | `/config/server/tasks/{task-id}` | 获取 task。 |
| `DELETE` | `/config/server/tasks/{task-id}` | 删除 task。 |
| `GET` | `/config/server/top-menus` | 获取 top menus。 |
| `GET` | `/config/server/preferences` | 获取默认用户偏好。 |
| `PUT` | `/config/server/preferences` | 设置默认用户偏好。 |
| `GET` | `/config/server/preferences.diff` | 获取默认 diff 偏好。 |
| `PUT` | `/config/server/preferences.diff` | 设置默认 diff 偏好。 |
| `GET` | `/config/server/preferences.edit` | 获取默认 edit 偏好。 |
| `PUT` | `/config/server/preferences.edit` | 设置默认 edit 偏好。 |
| `GET` | `/config/server/indexes` | 列出 indexes。 |
| `GET` | `/config/server/indexes/changes` | 获取 changes index。 |
| `GET` | `/config/server/indexes/changes/versions` | 列出 changes index versions。 |
| `GET` | `/config/server/indexes/changes/versions/85` | 获取 index version。 |
| `POST` | `/config/server/snapshot.indexes` | 创建 index snapshot。 |
| `POST` | `/config/server/indexes/{index-name}/snapshot` | 创建某 index snapshot。 |
| `POST` | `/config/server/indexes/{index-name}/versions/{index-version}/snapshot` | 创建某 index version snapshot。 |
| `POST` | `/config/server/indexes/{index-name}/versions/{index-version}/reindex` | reindex 某 index version。 |
| `GET` | `/config/server/experiments/{experiment-name}` | 获取 experiment。 |

### 5.5 `/groups/`

#### Group Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/groups/` | 列出 groups。 |
| `GET` | `/groups/?query=<query>` | 查询 groups。 |
| `GET` | `/groups/{group-id}` | 获取 group。 |
| `PUT` | `/groups/{group-name}` | 创建 group。 |
| `GET` | `/groups/{group-id}/detail` | 获取 group detail。 |
| `GET` | `/groups/{group-id}/name` | 获取 group name。 |
| `PUT` | `/groups/{group-id}/name` | 重命名 group。 |
| `GET` | `/groups/{group-id}/description` | 获取 group description。 |
| `PUT` | `/groups/{group-id}/description` | 设置 group description。 |
| `DELETE` | `/groups/{group-id}/description` | 删除 group description。 |
| `GET` | `/groups/{group-id}/options` | 获取 group options。 |
| `PUT` | `/groups/{group-id}/options` | 设置 group options。 |
| `GET` | `/groups/{group-id}/owner` | 获取 group owner。 |
| `PUT` | `/groups/{group-id}/owner` | 设置 group owner。 |
| `GET` | `/groups/{group-id}/log.audit` | 获取 audit log。 |
| `POST` | `/groups/{group-id}/index` | 重新索引 group。 |

#### Group Member Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/groups/{group-id}/members/` | 列出 members。 |
| `GET` | `/groups/{group-id}/members/{account-id}` | 获取 member。 |
| `PUT` | `/groups/{group-id}/members/{account-id}` | 添加 member。 |
| `POST` | `/groups/{group-id}/members` | 批量添加 members。 |
| `POST` | `/groups/{group-id}/members.add` | 批量添加 members 的别名形式。 |
| `DELETE` | `/groups/{group-id}/members/{account-id}` | 移除 member。 |
| `POST` | `/groups/{group-id}/members.delete` | 批量移除 members。 |

#### Subgroup Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/groups/{group-id}/groups/` | 列出 subgroups。 |
| `GET` | `/groups/{group-id}/groups/{group-id}` | 获取 subgroup。 |
| `PUT` | `/groups/{group-id}/groups/{group-id}` | 添加 subgroup。 |
| `POST` | `/groups/{group-id}/groups` | 批量添加 subgroups。 |
| `POST` | `/groups/{group-id}/groups.add` | 批量添加 subgroups 的别名形式。 |
| `DELETE` | `/groups/{group-id}/groups/{group-id}` | 移除 subgroup。 |
| `POST` | `/groups/{group-id}/groups.delete` | 批量移除 subgroups。 |

### 5.6 `/plugins/`

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/plugins/` | 列出 plugins。 |
| `PUT` | `/plugins/{plugin-id}.jar` | 安装 plugin。 |
| `GET` | `/plugins/{plugin-id}/gerrit~status` | 获取 plugin status。 |
| `POST` | `/plugins/{plugin-id}/gerrit~enable` | 启用 plugin。 |
| `POST` | `/plugins/{plugin-id}/gerrit~disable` | 禁用 plugin。 |
| `DELETE` | `/plugins/{plugin-id}` | 禁用 plugin。 |
| `POST` | `/plugins/{plugin-id}/gerrit~reload` | reload plugin。 |

### 5.7 `/projects/`

#### Project Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/` | 列出 projects。 |
| `GET` | `/projects/?query=<query>` | 查询 projects。 |
| `GET` | `/projects/{project-name}` | 获取 project。 |
| `PUT` | `/projects/{project-name}` | 创建 project。 |
| `GET` | `/projects/{project-name}/description` | 获取 project description。 |
| `PUT` | `/projects/{project-name}/description` | 设置 project description。 |
| `DELETE` | `/projects/{project-name}/description` | 删除 project description。 |
| `GET` | `/projects/{project-name}/parent` | 获取 parent project。 |
| `PUT` | `/projects/{project-name}/parent` | 设置 parent project。 |
| `GET` | `/projects/{project-name}/HEAD` | 获取 HEAD。 |
| `PUT` | `/projects/{project-name}/HEAD` | 设置 HEAD。 |
| `GET` | `/projects/{project-name}/statistics.git` | 获取 Git repository statistics。 |
| `GET` | `/projects/{project-name}/config` | 获取 project config。 |
| `PUT` | `/projects/{project-name}/config` | 设置 project config。 |
| `PUT` | `/projects/{project-name}/config:review` | 创建 config 变更用于 review。 |
| `POST` | `/projects/{project-name}/gc` | 执行 GC。 |
| `PUT` | `/projects/{project-name}/ban` | ban commit。 |
| `GET` | `/projects/{project-name}/access` | 获取 project access rights。 |
| `POST` | `/projects/{project-name}/access` | 增删改 project access rights。 |
| `PUT` | `/projects/{project-name}/access:review` | 创建 access rights 变更用于 review。 |
| `GET` | `/projects/{project-name}/check.access?account={account-id}&ref={ref}` | 检查某账号 access。 |
| `GET` | `/projects/{project-name}/commits:in` | 查询 commits 包含在哪些 refs。 |

#### Branch Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/{project-name}/branches/` | 列出 branches。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}` | 获取 branch。 |
| `PUT` | `/projects/{project-name}/branches/{branch-id}` | 创建 branch。 |
| `DELETE` | `/projects/{project-name}/branches/{branch-id}` | 删除 branch。 |
| `POST` | `/projects/{project-name}/branches:delete` | 批量删除 branches。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/files/{file-id}/content` | 获取 branch 文件内容。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/suggest_reviewers?q=J&n=5` | 为 branch 推荐 reviewers。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/suggest_reviewers?q=J&n=5&exclude-groups` | 推荐 reviewers，排除 groups。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/suggest_reviewers?q=J&reviewer-state=CC` | 推荐 CC。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/mergeable` | 获取 branch mergeable 信息。 |
| `GET` | `/projects/{project-name}/branches/{branch-id}/reflog` | 获取 reflog。 |

备注：官方 3.11.2 `rest-api-projects.html` 在 `exclude-groups` 和 `reviewer-state=CC` 两个说明行里显示了 `/changes/{project-name}/branches/...`，但同节主路径和 request 示例均为 `/projects/{project-name}/branches/...`。实现时应使用 `/projects/`。

#### Child Project Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/{project-name}/children/` | 列出 child projects。 |
| `GET` | `/projects/{project-name}/children/{project-name}` | 获取 child project。 |

#### Tag Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `PUT` | `/projects/{project-name}/tags/{tag-id}` | 创建 tag。 |
| `GET` | `/projects/{project-name}/tags/` | 列出 tags。 |
| `GET` | `/projects/{project-name}/tags/{tag-id}` | 获取 tag。 |
| `DELETE` | `/projects/{project-name}/tags/{tag-id}` | 删除 tag。 |
| `POST` | `/projects/{project-name}/tags:delete` | 批量删除 tags。 |

#### Commit Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/{project-name}/commits/{commit-id}` | 获取 commit。 |
| `GET` | `/projects/{project-name}/commits/{commit-id}/in` | 查询 commit 包含在哪些 refs。 |
| `GET` | `/projects/{project-name}/commits/{commit-id}/files/{file-id}/content` | 获取 commit 文件内容。 |
| `POST` | `/projects/{project-name}/commits/{commit-id}/cherrypick` | cherry-pick commit。 |
| `GET` | `/projects/{project-name}/commits/{commit-id}/files/` | 列出 commit files。 |

#### Dashboard Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/{project-name}/dashboards/` | 列出 dashboards。 |
| `GET` | `/projects/{project-name}/dashboards/{dashboard-id}` | 获取 dashboard。 |
| `PUT` | `/projects/{project-name}/dashboards/{dashboard-id}` | 创建 dashboard。 |
| `PUT` | `/projects/{project-name}/dashboards/{dashboard-id}` | 更新 dashboard。 |
| `DELETE` | `/projects/{project-name}/dashboards/{dashboard-id}` | 删除 dashboard。 |

#### Label Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/projects/{project-name}/labels/` | 列出 labels。 |
| `GET` | `/projects/{project-name}/labels/{label-name}` | 获取 label。 |
| `PUT` | `/projects/{project-name}/labels/{label-name}` | 创建 label。 |
| `PUT` | `/projects/{project-name}/labels/{label-name}` | 设置 label。 |
| `DELETE` | `/projects/{project-name}/labels/{label-name}` | 删除 label。 |
| `POST` | `/projects/{project-name}/labels/` | 批量更新 labels。 |
| `POST` | `/projects/{project-name}/labels:review` | 创建 labels config change 用于 review。 |

#### Submit Requirement Endpoints

| 方法 | 路径 | 用途 |
|---|---|---|
| `PUT` | `/projects/{project-name}/submit_requirements/{submit-requirement-name}` | 创建 submit requirement。 |
| `PUT` | `/projects/{project-name}/submit_requirements/{submit-requirement-name}` | 更新 submit requirement。 |
| `GET` | `/projects/{project-name}/submit_requirements/{submit-requirement-name}` | 获取 submit requirement。 |
| `GET` | `/projects/{project-name}/submit_requirements` | 列出 submit requirements。 |
| `DELETE` | `/projects/{project-name}/submit_requirements/{submit-requirement-name}` | 删除 submit requirement。 |
| `POST` | `/projects/{project-name}/submit_requirements/` | 批量更新 submit requirements。 |
| `POST` | `/projects/{project-name}/submit_requirements:review` | 创建 submit requirements config change 用于 review。 |

### 5.8 `/Documentation/`

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/Documentation/` | 搜索 Gerrit 内置文档。 |

## 6. 关键请求体模板

以下模板只列 Skill 高使用频率字段。完整字段以对应官方 JSON Entities 为准。

### 6.1 创建 Change：`ChangeInput`

```http
POST /changes/
Content-Type: application/json; charset=UTF-8
```

```json
{
  "project": "myProject",
  "branch": "master",
  "subject": "Implement feature X",
  "topic": "feature-x",
  "status": "NEW",
  "is_private": false,
  "work_in_progress": false,
  "base_change": "myProject~4247",
  "base_commit": "40-char-sha1"
}
```

说明：

- `project`、`branch`、`subject` 必填。
- `branch` 通常省略 `refs/heads/`。
- `base_change` 与 `base_commit` 互斥。
- 如果 subject/commit message 中没有 `Change-Id`，Gerrit 会生成。

### 6.2 添加 Reviewer：`ReviewerInput`

```http
POST /changes/{change-id}/reviewers
```

```json
{
  "reviewer": "alice@example.com",
  "state": "REVIEWER",
  "confirmed": true,
  "notify": "OWNER_REVIEWERS"
}
```

`state` 可为：

- `REVIEWER`
- `CC`
- `REMOVED`

`notify` 可为：

- `NONE`
- `OWNER`
- `OWNER_REVIEWERS`
- `ALL`

### 6.3 评审、投票、评论：`ReviewInput`

```http
POST /changes/{change-id}/revisions/current/review
```

```json
{
  "message": "Looks good to me.",
  "tag": "autogenerated:agent-review",
  "labels": {
    "Code-Review": 1,
    "Verified": 1
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

`drafts` 可为：

- `KEEP`
- `PUBLISH`
- `PUBLISH_ALL_REVISIONS`

Agent 建议：

- CI 或自动化评论使用 `tag: "autogenerated:<tool-name>"`，便于 UI 聚合隐藏旧消息。
- 只投票不留言时也可以只传 `labels`。
- patchset-level comment 使用 `/PATCHSET_LEVEL` 作为文件路径。

### 6.4 获取文件 diff

```http
GET /changes/{change-id}/revisions/current/files/{file-id}/diff?base={base-revision-id}
```

常用 query：

- `base=<revision-id>`：指定 base patch set。
- `intraline`：请求 intraline diff。
- `context=<n>`：上下文行数。
- `ignore-whitespace=IGNORE_NONE|IGNORE_TRAILING|IGNORE_LEADING_AND_TRAILING|IGNORE_ALL`

### 6.5 修改 topic：`TopicInput`

```http
PUT /changes/{change-id}/topic
```

```json
{
  "topic": "release-2026-05"
}
```

### 6.6 WIP / Ready：`WorkInProgressInput`

```http
POST /changes/{change-id}/wip
```

```json
{
  "message": "Need more local testing."
}
```

```http
POST /changes/{change-id}/ready
```

```json
{
  "message": "Ready for review."
}
```

### 6.7 Submit：`SubmitInput`

```http
POST /changes/{change-id}/submit
```

```json
{
  "notify": "ALL",
  "on_behalf_of": "1000001"
}
```

说明：

- `on_behalf_of` 需要目标 branch 上的 Submit On Behalf Of 权限。
- 若有 post approval diff，Gerrit 可能强制通知所有相关人员。

### 6.8 Abandon / Restore

```http
POST /changes/{change-id}/abandon
```

```json
{
  "message": "Obsolete after redesign.",
  "notify": "OWNER"
}
```

```http
POST /changes/{change-id}/restore
```

```json
{
  "message": "Restoring for the release branch.",
  "notify": "OWNER_REVIEWERS"
}
```

### 6.9 Rebase：`RebaseInput`

```http
POST /changes/{change-id}/rebase
```

```json
{
  "base": "myProject~4247",
  "allow_conflicts": false,
  "validation_options": {
    "key": "value"
  }
}
```

### 6.10 Cherry-pick：`CherryPickInput`

```http
POST /changes/{change-id}/revisions/current/cherrypick
```

```json
{
  "message": "Cherry pick feature X\n\nChange-Id: I....",
  "destination": "stable-3.11",
  "base": "40-char-sha1",
  "topic": "backport-feature-x",
  "allow_conflicts": false,
  "notify": "OWNER_REVIEWERS"
}
```

### 6.11 Change Edit：修改文件并发布

```http
PUT /changes/{change-id}/edit/src%2Fmain%2FApp.java
Content-Type: text/plain; charset=UTF-8

<new file content>
```

```http
POST /changes/{change-id}/edit:publish
```

```json
{
  "notify": "OWNER_REVIEWERS"
}
```

### 6.12 创建 Project：`ProjectInput`

```http
PUT /projects/{project-name}
```

```json
{
  "description": "Service repository",
  "submit_type": "MERGE_IF_NECESSARY",
  "owners": ["Project Owners"],
  "parent": "All-Projects",
  "create_empty_commit": true,
  "permissions_only": false
}
```

### 6.13 创建 Branch：`BranchInput`

```http
PUT /projects/{project-name}/branches/{branch-id}
```

```json
{
  "revision": "master"
}
```

### 6.14 创建 Tag：`TagInput`

```http
PUT /projects/{project-name}/tags/{tag-id}
```

```json
{
  "revision": "master",
  "message": "Release 1.0.0"
}
```

### 6.15 更新 Project Access：`ProjectAccessInput`

```http
POST /projects/{project-name}/access
```

```json
{
  "add": {
    "refs/heads/*": {
      "permissions": {
        "read": {
          "rules": {
            "group-id": {
              "action": "ALLOW",
              "force": false
            }
          }
        }
      }
    }
  },
  "remove": {}
}
```

如需走代码评审而不是直接生效：

```http
PUT /projects/{project-name}/access:review
```

### 6.16 更新 Labels / Submit Requirements

Label 直接更新：

```http
PUT /projects/{project-name}/labels/{label-name}
```

Label 走 review：

```http
POST /projects/{project-name}/labels:review
```

Submit requirement 直接更新：

```http
PUT /projects/{project-name}/submit_requirements/{submit-requirement-name}
```

Submit requirement 走 review：

```http
POST /projects/{project-name}/submit_requirements:review
```

## 7. 关键响应实体

### 7.1 `ChangeInfo`

高频字段：

- `id`：`<project>~<branch>~<Change-Id>` 格式的 triplet id。
- `_number`：change number。
- `project`、`branch`、`topic`。
- `change_id`：commit message 中的 `Change-Id`。
- `subject`。
- `status`：如 `NEW`、`MERGED`、`ABANDONED`。
- `created`、`updated`、`submitted`。
- `owner`：`AccountInfo`。
- `labels`：label 状态，需 `LABELS` 或 `DETAILED_LABELS`。
- `submit_requirements`：需 `SUBMIT_REQUIREMENTS`。
- `current_revision`、`revisions`：需 `CURRENT_REVISION` 或 `ALL_REVISIONS`。
- `messages`：需 `MESSAGES`。
- `reviewer_updates`：需 `REVIEWER_UPDATES`。
- `insertions`、`deletions`。
- `unresolved_comment_count`。
- `mergeable`、`submittable`。

### 7.2 `RevisionInfo`

高频字段：

- `_number`：patch set number。
- `created`。
- `uploader`。
- `ref`：可 fetch 的 ref，如 `refs/changes/97/97/1`。
- `fetch`：按协议提供 fetch URL、ref、命令。
- `commit`：需 `CURRENT_COMMIT` 或 `ALL_COMMITS`。
- `files`：需 `CURRENT_FILES` 或 `ALL_FILES`。
- `actions`：需 `CURRENT_ACTIONS`。

### 7.3 `FileInfo`

高频字段：

- `status`：`A` added、`D` deleted、`R` renamed、`C` copied、`W` rewritten；缺省表示 modified。
- `old_path`。
- `lines_inserted`、`lines_deleted`。
- `size_delta`、`size`。
- `old_mode`、`new_mode`。

### 7.4 `DiffInfo`

高频字段：

- `meta_a`、`meta_b`：旧/新文件元数据。
- `change_type`：修改类型。
- `intraline_status`。
- `diff_header`。
- `content`：diff content blocks。
- `web_links`。

### 7.5 `CommentInfo` / `CommentInput`

高频字段：

- `id`。
- `path`。
- `side`：`REVISION` 或 `PARENT`。
- `line`。
- `range`：范围评论。
- `message`。
- `updated`。
- `author`。
- `unresolved`。
- `in_reply_to`。

### 7.6 官方 JSON Entities 覆盖范围

官方页面中的 JSON Entities 数量较多，开发 Skill 时可按资源页查完整字段。3.11.2 文档中的实体覆盖如下：

- Access：`AccessSectionInfo`、`PermissionInfo`、`PermissionRuleInfo`、`ProjectAccessInfo`
- Accounts：`AccountDetailInfo`、`AccountExternalIdInfo`、`AccountInfo`、`AccountInput`、`AccountNameInput`、`AccountStateInfo`、`AccountStatusInput`、`AvatarInfo`、`CapabilityInfo`、`ContributorAgreementInfo`、`ContributorAgreementInput`、`DeleteDraftCommentsInput`、`DeletedDraftCommentInfo`、`DiffPreferencesInfo`、`DiffPreferencesInput`、`EditPreferencesInfo`、`EmailInfo`、`EmailInput`、`GpgKeyInfo`、`GpgKeysInput`、`HttpPasswordInput`、`OAuthTokenInfo`、`PreferencesInfo`、`PreferencesInput`、`QueryLimitInfo`、`SshKeyInfo`、`UsernameInput`、`DisplayNameInput`、`ProjectWatchInfo`
- Changes：`AbandonInput`、`ActionInfo`、`ApplyPatchInput`、`ApplyPatchPatchSetInput`、`ApprovalInfo`、`AttentionSetInfo`、`AttentionSetInput`、`BlameInfo`、`ChangeEditInput`、`ChangeEditMessageInput`、`ChangeEditIdentityInput`、`ChangeInfo`、`ChangeInput`、`ChangeMessageInfo`、`CherryPickInput`、`CommentInfo`、`CommentInput`、`CommentRange`、`ContextLine`、`CommitInfo`、`CommitMessageInfo`、`CommitMessageInput`、`DeleteChangeMessageInput`、`DeleteCommentInput`、`DeleteReviewerInput`、`DeleteVoteInput`、`DescriptionInput`、`DiffContent`、`DiffFileMetaInfo`、`DiffInfo`、`DiffIntralineInfo`、`DiffWebLinkInfo`、`ApplyProvidedFixInput`、`CustomKeyedValuesInput`、`EditFileInfo`、`EditInfo`、`FetchInfo`、`FileInfo`、`FixInput`、`FixSuggestionInfo`、`FixReplacementInfo`、`GitPersonInfo`、`GroupBaseInfo`、`HashtagsInput`、`IncludedInInfo`、`LabelInfo`、`MergeableInfo`、`MergeInput`、`MergePatchSetInput`、`MoveInput`、`NotifyInfo`、`ParentInfo`、`PrivateInput`、`ProblemInfo`、`PublishChangeEditInput`、`PureRevertInfo`、`PushCertificateInfo`、`RangeInfo`、`RebaseChangeEditInput`、`RebaseInput`、`RebaseChainInfo`、`RelatedChangeAndCommitInfo`、`RelatedChangesInfo`、`Requirement`、`RestoreInput`、`RevertInput`、`RevertSubmissionInfo`、`ReviewInfo`、`ReviewerUpdateInfo`、`ReviewInput`、`ReviewResult`、`ReviewerInfo`、`ReviewerInput`、`ReviewerResult`、`RevisionInfo`、`RobotCommentInfo`、`RobotCommentInput`、`RuleInput`、`SubmitInput`、`SubmitRecord`、`SubmitRecordInfo`、`SubmitRequirementExpressionInfo`、`SubmitRequirementInput`、`SubmitRequirementResultInfo`、`SubmittedTogetherInfo`、`SuggestedReviewerInfo`、`TopicInput`、`TrackingIdInfo`、`VotingRangeInfo`、`WebLinkInfo`、`WorkInProgressInput`
- Config：`AccountsConfigInfo`、`AuthInfo`、`CacheInfo`、`CacheOperationInput`、`CapabilityInfo`、`ChangeConfigInfo`、`ChangeIndexConfigInfo`、`CheckAccountExternalIdsInput`、`CheckAccountExternalIdsResultInfo`、`CheckAccountsInput`、`CheckAccountsResultInfo`、`CheckGroupsInput`、`CheckGroupsResultInfo`、`ConsistencyCheckInfo`、`ConsistencyCheckInput`、`ConsistencyProblemInfo`、`ConfigUpdateInfo`、`ConfigUpdateEntryInfo`、`ExperimentInfo`、`DownloadInfo`、`DownloadSchemeInfo`、`EmailConfirmationInput`、`EntriesInfo`、`GerritInfo`、`IndexConfigInfo`、`HitRatioInfo`、`IndexChangesInput`、`JvmSummaryInfo`、`MemSummaryInfo`、`MetadataInfo`、`PluginConfigInfo`、`ReceiveInfo`、`VersionInfo`、`ServerInfo`、`SnapshotIndex.Input`、`SshdInfo`、`SuggestInfo`、`SummaryInfo`、`TaskInfo`、`TaskSummaryInfo`、`ThreadSummaryInfo`、`TopMenuEntryInfo`、`TopMenuItemInfo`、`UserConfigInfo`、`CleanChanges.Input`
- Groups：`GroupAuditEventInfo`、`GroupInfo`、`GroupInput`、`GroupOptionsInfo`、`GroupOptionsInput`、`GroupsInput`、`MembersInput`
- Plugins：`PluginInfo`、`PluginInput`
- Projects：`AccessCheckInfo`、`AutoCloseableChangesCheckInput`、`AutoCloseableChangesCheckResult`、`BanInput`、`BanResultInfo`、`BranchInfo`、`BranchInput`、`CheckProjectInput`、`CheckProjectResultInfo`、`CommentLinkInfo`、`CommentLinkInput`、`ConfigInfo`、`ConfigInput`、`ConfigParameterInfo`、`DashboardInfo`、`DashboardInput`、`DashboardSectionInfo`、`DeleteLabelInput`、`DeleteBranchesInput`、`DeleteTagsInput`、`GCInput`、`HeadInput`、`IndexProjectInput`、`InheritedBooleanInfo`、`LabelDefinitionInfo`、`LabelDefinitionInput`、`LabelTypeInfo`、`MaxObjectSizeLimitInfo`、`BatchLabelInput`、`BatchSubmitRequirementInput`、`ProjectAccessInput`、`ProjectDescriptionInput`、`ProjectInfo`、`ProjectInput`、`ProjectParentInput`、`ReflogEntryInfo`、`RepositoryStatisticsInfo`、`SubmitRequirementInfo`、`SubmitRequirementInput`、`SubmitTypeInfo`、`TagInfo`、`TagInput`
- Documentation：`DocResult`

## 8. 常见工作流

### 8.1 验证连接与身份

```http
GET /config/server/version
GET /a/accounts/self/detail
GET /a/accounts/self/capabilities
```

### 8.2 查询待我评审的变更

```http
GET /a/changes/?q=reviewer:self+-owner:self+status:open&o=CURRENT_REVISION&o=DETAILED_ACCOUNTS&o=DETAILED_LABELS&o=SUBMIT_REQUIREMENTS&n=25
```

### 8.3 获取某个变更的完整评审上下文

```http
GET /a/changes/{change-id}/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT&o=CURRENT_FILES&o=DETAILED_ACCOUNTS&o=DETAILED_LABELS&o=SUBMIT_REQUIREMENTS&o=MESSAGES&o=REVIEWER_UPDATES
GET /a/changes/{change-id}/comments
GET /a/changes/{change-id}/drafts
GET /a/changes/{change-id}/revisions/current/files/
```

### 8.4 拉取文件 diff 并发表评论

```http
GET /a/changes/{change-id}/revisions/current/files/src%2Fmain%2FApp.java/diff?context=50&intraline
POST /a/changes/{change-id}/revisions/current/review
```

```json
{
  "message": "Reviewed by agent.",
  "comments": {
    "src/main/App.java": [
      {
        "line": 42,
        "message": "This branch can be simplified.",
        "unresolved": true
      }
    ]
  },
  "labels": {
    "Code-Review": 0
  }
}
```

### 8.5 提交 change 前检查

```http
GET /a/changes/{change-id}/detail?o=DETAILED_LABELS&o=SUBMIT_REQUIREMENTS&o=CURRENT_ACTIONS
GET /a/changes/{change-id}/revisions/current/mergeable
GET /a/changes/{change-id}/submitted_together
POST /a/changes/{change-id}/submit
```

### 8.6 自动修复并上传 patch set

REST-only 路线：

```http
PUT /a/changes/{change-id}/edit/src%2Fmain%2FApp.java
POST /a/changes/{change-id}/edit:publish
POST /a/changes/{change-id}/revisions/current/review
```

Git 路线：

1. 从 `ChangeInfo.revisions[current].fetch` 获取 fetch ref。
2. 本地修改并 commit，保留原 `Change-Id`。
3. push 到 `refs/for/<branch>`。
4. 再用 REST 添加评论或 reviewer。

### 8.7 管理项目配置但走评审

```http
PUT /a/projects/{project-name}/config:review
PUT /a/projects/{project-name}/access:review
POST /a/projects/{project-name}/labels:review
POST /a/projects/{project-name}/submit_requirements:review
```

这些接口会在 `refs/meta/config` 创建待评审 change，适合 Agent 做低风险配置修改。

## 9. 实现细节与坑位

- Gerrit REST 是 REST-like，很多动作是 `POST /resource/action` 或 `POST /resource:action`。
- `DELETE` 有时有 `POST .../delete` 替代形式，适合受代理限制或需要 body 的场景。
- JSON body 中未识别字段通常会被忽略；Skill 仍应做本地 schema 校验，避免拼错字段静默失效。
- 字符串响应也可能带 XSSI 前缀，解析时不要只处理对象和数组。
- 文件内容接口可能返回 base64 或 `text/plain`，按官方 endpoint 和 `Content-Type` 分流处理。
- 对 change 查询不要默认使用所有 `o=`，否则大项目上会慢。先 summary，再按需 detail。
- `robotcomments` 在 3.11.2 文档中标为 deprecated，新实现应优先使用普通 comments 或 checks/CI 自己的 comment tag。
- `submit`、`abandon`、`restore`、`rebase`、`move`、`private` 等动作都受权限和状态限制，失败时应把 Gerrit 返回的纯文本错误透传给用户。
- 对 `project`、`branch`、`file` 参数做集中 URL encode，不要让各工具重复实现。
- 保存 change 引用时同时存 `_number`、`project`、`branch`、`change_id`，调用时转成 `<project>~<_number>`。
- 插件可能扩展 REST API。核心 `/plugins/` 只负责插件管理，不代表插件自定义 endpoint 列表。

## 10. 推荐 Skill 最小能力集

第一阶段建议实现：

- `gerrit_get_version`
- `gerrit_whoami`
- `gerrit_query_changes`
- `gerrit_get_change`
- `gerrit_list_files`
- `gerrit_get_diff`
- `gerrit_list_comments`
- `gerrit_review`
- `gerrit_add_reviewer`
- `gerrit_submit`
- `gerrit_abandon`
- `gerrit_rebase`
- `gerrit_set_wip`
- `gerrit_set_ready`
- `gerrit_list_projects`
- `gerrit_get_project`
- `gerrit_list_branches`

第二阶段再补：

- change edit 文件修改与 publish
- project access / labels / submit requirements
- group/account 管理
- cache、index、task 等管理员接口
- plugin 管理

## 11. 最小 curl 调试模板

```bash
GERRIT_BASE_URL="https://gerrit.example.com"
GERRIT_USERNAME="alice"
GERRIT_HTTP_PASSWORD="***"

curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/config/server/version"
```

解析 JSON：

```bash
curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/accounts/self/detail" |
sed "1{/^)]}'/d;}"
```

提交 review：

```bash
curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Content-Type: application/json; charset=UTF-8" \
  -X POST \
  "$GERRIT_BASE_URL/a/changes/myProject~4247/revisions/current/review" \
  -d '{
    "message": "Reviewed by agent.",
    "labels": {"Code-Review": 1}
  }'
```
