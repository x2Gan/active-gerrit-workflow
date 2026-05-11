# Gerrit Skill 分层封装方案

> 目标：基于 Gerrit Code Review `3.11.2` REST API，设计一组可供 Agent 稳定调用的 Gerrit Skills。
>
> 方案重点：
>
> 1. 默认使用 Basic Auth 鉴权，也就是用户名 + Gerrit HTTP Password。
> 2. 将稳定、重复、易错的 REST API 调用固化成脚本。
> 3. 将高频查询结果固化为标准输出结构，并对可缓存结果设计缓存策略。
> 4. 将 Gerrit 能力拆成基础能力 Skill 与业务流程 Skill 两层。

## 1. 总体结论

建议将 Gerrit 能力拆成两个 Skill：

| Skill | 定位 | 责任边界 |
|---|---|---|
| `active-gerrit` | Gerrit REST API 基础能力封装，也是 Gerrit 能力的 fallback 兜底。 | 鉴权、HTTP client、XSSI 清理、URL 编码、错误处理、REST endpoint 脚本化、标准结果 schema、基础 Gerrit 工作流。 |
| `active-gerrit-workflow` | 结合具体业务流程的高级 Skill 封装。 | 编排评审流程、发布流程、质量门禁、团队规范、跨 change 操作、业务语义判断；必要时调用 `active-gerrit`。 |

两者关系：

- `active-gerrit` 是底座，尽量不包含具体团队业务规则。
- `active-gerrit-workflow` 是上层编排，可以依赖 `active-gerrit` 的脚本和标准输出。
- 当高级流程 Skill 遇到未覆盖的 Gerrit 低层操作时，应降级调用 `active-gerrit`。
- REST API 文档和通用 client 不在两个 Skill 中重复维护，权威实现放在 `active-gerrit`。
- 业务流程、审批规则、发布策略、团队约定只放在 `active-gerrit-workflow`。

建议将本项目产出拆成四类内容：

```text
active-gerrit/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── gerrit-rest-api-3.11.2.md
│   ├── core-workflows.md
│   └── result-schemas.md
└── scripts/
    ├── gerrit_client.py
    ├── gerrit_cli.py
    ├── gerrit_cache.py
    └── gerrit_errors.py

active-gerrit-workflow/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── business-workflows.md
│   ├── review-policies.md
│   ├── release-policies.md
│   └── escalation-rules.md
└── scripts/
    ├── workflow_cli.py
    ├── workflow_rules.py
    └── workflow_reports.py
```

设计原则：

- 两个 `SKILL.md` 都保持精简，只告诉 Agent 什么时候使用该 Skill、先读什么 reference、优先调用哪些脚本、写操作的安全规则。
- `active-gerrit/references/` 存放 REST API 与通用 Gerrit 工作流。
- `active-gerrit-workflow/references/` 存放业务流程、团队规范、发布规则、评审策略。
- `active-gerrit/scripts/` 固化 REST 调用、鉴权、XSSI 清理、URL 编码、错误处理、结果标准化。
- `active-gerrit-workflow/scripts/` 固化业务流程编排和报告生成，底层 Gerrit 操作优先调用 `active-gerrit`。
- Agent 面向任务调用脚本，不直接拼底层 REST path；高级流程也不直接绕过基础 Skill 重写 REST client。
- 所有写操作默认有清晰的 `--dry-run`、`--notify`、`--reason/message`、`--yes` 或显式确认机制。

## 2. Skill 分层与目录设计

### 2.1 `active-gerrit`

`active-gerrit` 是 Gerrit REST API 基础能力层。它应能独立工作，并作为所有 Gerrit 操作的 fallback。

目录建议：

```text
active-gerrit/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── gerrit-rest-api-3.11.2.md
│   ├── core-workflows.md
│   └── result-schemas.md
└── scripts/
    ├── gerrit_client.py
    ├── gerrit_cli.py
    ├── gerrit_cache.py
    └── gerrit_errors.py
```

`active-gerrit/SKILL.md` 建议控制在 300 到 500 行内，只放 Agent 必须遵守的基础能力规则：

- 触发条件：用户要求查询 Gerrit、评审 change、读 diff、发评论、投票、submit、管理 reviewer、查询项目/分支/标签，或者其他 Skill 需要 Gerrit 兜底能力时使用。
- 优先流程：
  - 先运行 `doctor` 或 `whoami` 验证连接。
  - 查询 change 时先用 summary，再按需 detail。
  - 读 diff 前先解析 `change-id` 和 `revision-id`。
  - 写评论或投票前确认 patch set 是目标 patch set。
  - submit/rebase/abandon 等动作前刷新 change detail。
- 何时读取 reference：
  - API 字段不明确时读 `references/gerrit-rest-api-3.11.2.md`。
  - 通用 Gerrit 工作流不明确时读 `references/core-workflows.md`。
  - 需要解释脚本输出结构时读 `references/result-schemas.md`。
- 安全规则：
  - 不打印密码、Authorization header、cookie、token。
  - 高风险写操作必须先展示操作摘要。
  - submit 前必须刷新 submit requirements 和 mergeable 状态。
  - 删除、项目权限、插件、缓存、索引类管理员操作默认不自动执行。

`active-gerrit` 不应包含：

- 具体团队的评审策略。
- 发布审批流程。
- 业务域风险判断。
- 跨系统协作规则。

这些内容放入 `active-gerrit-workflow`。

### 2.2 `active-gerrit` references

建议拆分如下：

| 文件 | 内容 | 读取时机 |
|---|---|---|
| `gerrit-rest-api-3.11.2.md` | 从 `doc/Gerrit REST API.md` 精简迁移，保留端点、实体、payload。 | 需要查 endpoint、字段、参数时。 |
| `core-workflows.md` | 待我评审、读取 diff、发表评论、submit 前检查、change edit、项目配置评审等通用 Gerrit 流程。 | 用户请求是通用 Gerrit 流程时。 |
| `result-schemas.md` | 脚本标准输出结构、缓存 key、错误结构。 | 需要消费脚本 JSON 输出时。 |

### 2.3 `active-gerrit` scripts

建议脚本以 Python 为主，原因是标准库足够处理 HTTP、JSON、argparse、base64、缓存文件；如后续需要更好体验，可再引入 `requests`。

建议只暴露一个稳定 CLI 入口：

```bash
python scripts/gerrit_cli.py <command> [options]
```

内部模块：

- `gerrit_client.py`：低层 HTTP client。
- `gerrit_errors.py`：HTTP 错误、Gerrit 错误文本、权限提示映射。
- `gerrit_cache.py`：只缓存非敏感、可复用查询结果。
- `gerrit_cli.py`：命令行入口，输出标准 JSON。

### 2.4 `active-gerrit-workflow`

`active-gerrit-workflow` 是业务流程层。它不重复实现 Gerrit REST client，而是编排 `active-gerrit` 的命令和结果。

目录建议：

```text
active-gerrit-workflow/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── business-workflows.md
│   ├── review-policies.md
│   ├── release-policies.md
│   └── escalation-rules.md
└── scripts/
    ├── workflow_cli.py
    ├── workflow_rules.py
    └── workflow_reports.py
```

`active-gerrit-workflow/SKILL.md` 关注：

- 触发条件：用户提出业务目标，而不是单个 Gerrit API 操作。例如“帮我处理本周待评审列表”“检查 release 分支是否可合入”“按团队规范生成评审结论”“发布前检查所有关联 change”。
- 依赖规则：需要 Gerrit 底层数据时，优先调用 `active-gerrit` 的脚本或遵循 `active-gerrit` 的结果 schema。
- 编排规则：把多个 Gerrit 操作组合成业务流程，例如查询待评审列表、筛选高风险 change、读取 diff、生成报告、必要时发表评论。
- 降级规则：如果流程层没有覆盖某个 Gerrit 操作，明确回退到 `active-gerrit`。
- 安全规则：业务流程中的 submit、abandon、rebase、权限变更等高风险动作仍遵守 `active-gerrit` 的写操作保护策略。

`active-gerrit-workflow` 可以包含：

- 团队评审 checklist。
- 发布分支策略。
- owner/reviewer 分派规则。
- 风险分级规则。
- 变更报告模板。
- 多 change 批处理流程。
- 与内部业务系统或人工审批流程的衔接说明。

`active-gerrit-workflow` 不应包含：

- Gerrit Basic Auth 具体实现。
- XSSI 清理逻辑。
- Gerrit endpoint 全量文档。
- 通用 REST 请求封装。

## 3. 鉴权设计

### 3.1 默认鉴权：Basic Auth

本方案将“base 鉴权”按 Gerrit REST 的 Basic Auth 处理：用户名 + Gerrit HTTP Password。

默认配置：

```bash
export GERRIT_BASE_URL="https://gerrit.example.com"
export GERRIT_AUTH_TYPE="basic"
export GERRIT_USERNAME="alice"
export GERRIT_HTTP_PASSWORD="********"
```

可选配置：

```bash
export GERRIT_VERIFY_SSL="true"
export GERRIT_TIMEOUT_SECONDS="30"
export GERRIT_DEFAULT_NOTIFY="OWNER_REVIEWERS"
export GERRIT_CACHE_DIR=".cache/gerrit"
```

Basic Auth 请求规则：

- 需要认证的 REST 请求统一使用 `/a/` 前缀。
- `Authorization: Basic <base64(username:http_password)>` 由 client 统一生成。
- 不允许在日志中打印 Authorization header。
- 推荐从环境变量读取密码，避免命令行参数进入 shell history。
- `GERRIT_HTTP_PASSWORD` 是 Gerrit UI 中生成的 HTTP Password，不一定是登录 UI 的密码。

示例：

```http
GET /a/accounts/self/detail
Authorization: Basic <redacted>
Accept: application/json
```

### 3.2 鉴权抽象

建议在 client 中设计 `AuthProvider` 抽象，第一阶段只实现 `basic`，其他类型保留占位。

```text
AuthProvider
├── BasicAuthProvider       已实现，默认
├── BearerTokenProvider     预留
├── AccessTokenProvider     预留
├── CookieXsrfProvider      预留
└── AnonymousProvider       预留
```

### 3.3 其他鉴权预留

| 鉴权类型 | 配置占位 | 状态 | 说明 |
|---|---|---|---|
| `basic` | `GERRIT_USERNAME`、`GERRIT_HTTP_PASSWORD` | 第一阶段实现 | 默认方案，服务端 Agent 最稳定。 |
| `bearer` | `GERRIT_BEARER_TOKEN` | 预留 | 如果企业网关或代理支持 Bearer Token，可扩展。 |
| `access_token` | `GERRIT_ACCESS_TOKEN` | 预留 | Gerrit 支持 query 参数 `access_token`。 |
| `cookie_xsrf` | `GERRIT_COOKIE`、`GERRIT_XSRF_TOKEN` | 预留 | 浏览器 cookie 场景，mutation 需要 `X-Gerrit-Auth`。 |
| `anonymous` | 无 | 预留 | 只访问公开 GET endpoint。 |

实现建议：

```text
if auth_type == "basic":
    path = "/a" + path
    headers["Authorization"] = make_basic_header(username, password)
elif auth_type == "access_token":
    query["access_token"] = token
elif auth_type == "cookie_xsrf":
    headers["Cookie"] = cookie
    headers["X-Gerrit-Auth"] = xsrf_token
elif auth_type == "bearer":
    headers["Authorization"] = "Bearer <token>"
elif auth_type == "anonymous":
    pass
```

## 4. 运行依赖与前置环境检查

部署 `active-gerrit` 和 `active-gerrit-workflow` 前，应先做依赖检查。`active-gerrit` 是基础层，因此依赖检查也应主要固化在 `active-gerrit/scripts/gerrit_cli.py doctor` 中；`active-gerrit-workflow` 的 doctor 只需要检查自己能否调用基础层。

### 4.1 必需系统命令

| 依赖 | 必需性 | 用途 | 检查命令 |
|---|---|---|---|
| `python3` | 必需 | 运行 `gerrit_client.py`、`gerrit_cli.py`、`workflow_cli.py`。 | `python3 --version` |
| `curl` | 必需 | 最小连通性验证、部署排障、fallback HTTP 调试。 | `curl --version` |
| `git` | 必需 | 后续 Git + REST 混合工作流、fetch patch set、识别本地仓库。 | `git --version` |
| `sed` | 必需 | shell 调试时去除 XSSI 前缀。 | `sed --version` 或 `sed -n '1p'` |
| `env` | 必需 | 检查环境变量和执行可移植脚本。 | `env` |

说明：

- 即使 Python client 可以直接发 HTTP 请求，也建议把 `curl` 列为必需依赖。它是部署和排障时最可靠的最小验证工具。
- `git` 对纯 REST 查询不是必需，但对 Gerrit 场景非常关键，尤其是 patch set fetch、cherry-pick、本地修改后 push review，因此按项目必需依赖处理。
- 如果目标部署环境是极简容器，至少需要安装 `python3`、`curl`、`git`。

### 4.2 必需 Python 能力

第一阶段建议只依赖 Python 标准库，降低部署成本。

必需标准库：

| 模块 | 用途 |
|---|---|
| `argparse` | CLI 参数解析。 |
| `base64` | 生成 Basic Auth header。 |
| `datetime` | 输出 `fetched_at`、缓存 TTL 判断。 |
| `hashlib` | 生成缓存 key。 |
| `http.client` 或 `urllib.request` | 发送 HTTP 请求。 |
| `json` | JSON 序列化和解析。 |
| `os` | 读取环境变量。 |
| `pathlib` | 缓存目录和文件路径。 |
| `ssl` | SSL 验证配置。 |
| `subprocess` | `active-gerrit-workflow` 调用 `active-gerrit` CLI。 |
| `sys` | CLI exit code。 |
| `urllib.parse` | URL encode 与 query 构造。 |

最低版本建议：

```text
Python >= 3.9
```

原因：

- Python 3.9 在多数服务器环境中比较常见。
- 标准库足够完成第一阶段功能。
- 后续如果使用类型提示和更现代语法，可再提高到 Python 3.10+。

### 4.3 可选系统命令

| 依赖 | 必需性 | 用途 | 检查命令 |
|---|---|---|---|
| `jq` | 可选 | 命令行查看 JSON 输出。 | `jq --version` |
| `openssl` | 可选 | 调试 TLS、证书链、代理问题。 | `openssl version` |
| `ssh` | 可选 | Gerrit SSH fetch 或 SSH API 场景。 | `ssh -V` |
| `gh` | 可选 | 如果后续 workflow 需要联动 GitHub。 | `gh --version` |
| `rg` | 可选 | 本地代码搜索和报告生成。 | `rg --version` |

可选依赖不应阻塞 `active-gerrit` 基础能力启动，但 `doctor` 应在输出中标记缺失。

### 4.4 可选 Python 第三方包

第一阶段不强制第三方包。后续可选：

| 包 | 必需性 | 用途 |
|---|---|---|
| `requests` | 可选 | 简化 HTTP client、代理、超时和 TLS 配置。 |
| `pydantic` | 可选 | 定义严格输出 schema。 |
| `PyYAML` | 可选 | 读取 workflow/policy YAML 配置。 |

设计要求：

- 如果引入第三方包，必须提供 `requirements.txt` 或 `pyproject.toml`。
- `doctor` 必须能检查第三方包是否安装。
- 缺少可选包时，应降级到标准库实现或明确提示对应功能不可用。

### 4.5 必需环境变量

Basic Auth 模式下必需：

| 变量 | 必需性 | 说明 |
|---|---|---|
| `GERRIT_BASE_URL` | 必需 | Gerrit Web 根地址，例如 `https://gerrit.example.com`。 |
| `GERRIT_USERNAME` | 必需 | Gerrit 用户名。 |
| `GERRIT_HTTP_PASSWORD` | 必需 | Gerrit UI 中生成的 HTTP Password。 |

推荐配置：

| 变量 | 必需性 | 默认值 | 说明 |
|---|---|---|---|
| `GERRIT_AUTH_TYPE` | 可选 | `basic` | 当前实现默认 Basic Auth。 |
| `GERRIT_VERIFY_SSL` | 可选 | `true` | 是否验证 TLS 证书。 |
| `GERRIT_TIMEOUT_SECONDS` | 可选 | `30` | HTTP 请求超时。 |
| `GERRIT_DEFAULT_NOTIFY` | 可选 | `OWNER_REVIEWERS` | 写操作默认通知策略。 |
| `GERRIT_CACHE_DIR` | 可选 | `.cache/gerrit` | 本地缓存目录。 |

预留鉴权变量：

| 变量 | 对应鉴权 | 状态 |
|---|---|---|
| `GERRIT_BEARER_TOKEN` | `bearer` | 预留。 |
| `GERRIT_ACCESS_TOKEN` | `access_token` | 预留。 |
| `GERRIT_COOKIE` | `cookie_xsrf` | 预留。 |
| `GERRIT_XSRF_TOKEN` | `cookie_xsrf` | 预留。 |

### 4.6 `doctor` 前置检查清单

`active-gerrit` 必须实现：

```bash
python scripts/gerrit_cli.py doctor
```

检查项：

| 检查项 | 必需性 | 失败处理 |
|---|---|---|
| `python3` 版本 >= 3.9 | 必需 | 失败并提示升级 Python。 |
| `curl` 存在 | 必需 | 失败并提示安装 curl。 |
| `git` 存在 | 必需 | 失败并提示安装 git。 |
| 环境变量 `GERRIT_BASE_URL` 存在 | 必需 | 失败并提示配置。 |
| Basic Auth 变量存在 | Basic 模式必需 | 失败并提示配置用户名和 HTTP Password。 |
| `GERRIT_BASE_URL` 格式合法 | 必需 | 失败并提示应为 http(s) URL。 |
| TLS 验证可用 | 必需，除非关闭 | 失败并提示证书或 `GERRIT_VERIFY_SSL=false`。 |
| `GET /config/server/version` 成功 | 必需 | 失败并输出 HTTP 状态和排障建议。 |
| `GET /accounts/self/detail` 成功 | Basic 模式必需 | 失败并提示鉴权或权限问题。 |
| XSSI 前缀可清理 | 必需 | 失败并提示响应不是预期 Gerrit JSON。 |
| 缓存目录可创建/写入 | 可选但推荐 | 警告并禁用缓存。 |
| 可选命令 `jq`、`ssh`、`openssl` | 可选 | 输出 warning。 |

`doctor` 标准输出示例：

```json
{
  "ok": true,
  "command": "doctor",
  "data": {
    "dependencies": {
      "python3": {"ok": true, "version": "3.11.6"},
      "curl": {"ok": true, "version": "8.5.0"},
      "git": {"ok": true, "version": "2.43.0"},
      "jq": {"ok": false, "required": false}
    },
    "environment": {
      "GERRIT_BASE_URL": {"ok": true, "value": "https://gerrit.example.com"},
      "GERRIT_AUTH_TYPE": {"ok": true, "value": "basic"},
      "GERRIT_USERNAME": {"ok": true},
      "GERRIT_HTTP_PASSWORD": {"ok": true, "redacted": true}
    },
    "gerrit": {
      "version": "3.11.2",
      "whoami": {
        "account_id": 1000001,
        "username": "alice"
      }
    }
  },
  "warnings": []
}
```

### 4.7 `curl` 最小验证命令

部署排障时可以直接运行：

```bash
curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/config/server/version"
```

验证当前账号：

```bash
curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/accounts/self/detail" |
sed "1{/^)]}'/d;}"
```

如果使用自签名证书临时排障，可加 `-k`，但不建议作为长期配置：

```bash
curl -k -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/config/server/version"
```

### 4.8 `active-gerrit-workflow` 前置检查

`active-gerrit-workflow` 的前置检查不重复检查 Gerrit REST 细节，只检查：

| 检查项 | 必需性 | 说明 |
|---|---|---|
| 能执行 `active-gerrit` 的 `doctor` | 必需 | 基础层必须可用。 |
| 能执行 `active-gerrit` 的 `query-changes` | 必需 | 流程层依赖基础查询。 |
| 业务 reference 文件存在 | 必需 | 如 `business-workflows.md`、`review-policies.md`。 |
| 流程脚本可执行 | 必需 | `workflow_cli.py`。 |
| 可选 policy 配置存在 | 可选 | 无配置时使用默认流程。 |

建议命令：

```bash
python scripts/workflow_cli.py doctor
```

该命令内部应调用：

```bash
python ../active-gerrit/scripts/gerrit_cli.py doctor
```

如果部署时两个 Skill 不在相邻目录，应通过环境变量指定基础层路径：

```bash
export ACTIVE_GERRIT_HOME="/path/to/active-gerrit"
```

## 5. HTTP Client 固化能力

以下能力必须固化在 `gerrit_client.py`，不应让 Agent 临时拼装：

| 能力 | 说明 |
|---|---|
| Base URL 归一化 | 移除末尾 `/`，统一拼接 `/a/` 与 path。 |
| 鉴权注入 | 默认 Basic Auth，预留其他 AuthProvider。 |
| Header 注入 | `Accept: application/json`、`Content-Type`、可选 trace/deadline。 |
| XSSI 清理 | 自动去掉 Gerrit JSON 前缀 `)]}'`。 |
| JSON 解析 | 空响应返回 `null`，纯文本响应返回文本结构。 |
| URL 编码 | project、branch、file、change、account 等参数集中编码。 |
| Query 构造 | 支持重复参数，例如多个 `o=`、多个 `q=`。 |
| 错误映射 | 将 `401/403/404/409` 等转为稳定 JSON 错误。 |
| 请求追踪 | 支持 `trace=<id>` 或 `X-Gerrit-Trace`。 |
| Deadline | 支持 `X-Gerrit-Deadline`。 |
| 写操作 refs | 支持 `X-Gerrit-UpdatedRef-Enabled: true`。 |
| 输出标准化 | 所有 CLI command 输出统一 envelope。 |

标准输出 envelope：

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

错误输出：

```json
{
  "ok": false,
  "command": "submit",
  "error": {
    "type": "GerritConflict",
    "status": 409,
    "message": "Change is not ready to submit",
    "hint": "Refresh submit requirements and check missing labels."
  },
  "data": null,
  "warnings": []
}
```

## 6. 可以固化成脚本的 REST API 请求

本节的 REST API 请求主要固化在 `active-gerrit/scripts/gerrit_cli.py`。这些脚本输出稳定 JSON，供 Agent 直接消费，也供 `active-gerrit-workflow` 编排复用。

`active-gerrit-workflow` 不直接固化底层 REST API，而是固化“流程命令”。流程命令内部调用 `active-gerrit` 的脚本，例如先查询 change detail，再根据业务规则筛选风险，最后生成评审报告或评论草稿。

### 6.1 第一优先级：基础与只读查询

这些请求稳定、低风险、调用频率高，应该第一批脚本化。

| 脚本命令 | REST API | 说明 |
|---|---|---|
| `doctor` | `GET /config/server/version` + `GET /accounts/self/detail` | 验证 URL、鉴权、XSSI 解析。 |
| `version` | `GET /config/server/version` | 获取 Gerrit 版本。 |
| `whoami` | `GET /accounts/self/detail` | 获取当前账号。 |
| `my-capabilities` | `GET /accounts/self/capabilities` | 获取当前账号 capability。 |
| `query-accounts` | `GET /accounts/?q=...` | 解析用户名、邮箱、账号。 |
| `query-changes` | `GET /changes/?q=...&o=...` | Gerrit Skill 最核心查询。 |
| `get-change` | `GET /changes/{change-id}/detail?o=...` | 获取 change detail。 |
| `get-change-summary` | `GET /changes/{change-id}` | 轻量获取 change。 |
| `list-reviewers` | `GET /changes/{change-id}/reviewers/` | 获取 reviewers。 |
| `list-comments` | `GET /changes/{change-id}/comments` | 获取全量 published comments。 |
| `list-drafts` | `GET /changes/{change-id}/drafts` | 获取当前用户 drafts。 |
| `list-messages` | `GET /changes/{change-id}/messages` | 获取 change messages。 |
| `list-files` | `GET /changes/{change-id}/revisions/{revision-id}/files/` | 获取 patch set 文件列表。 |
| `get-diff` | `GET /changes/{change-id}/revisions/{revision-id}/files/{file-id}/diff` | 获取文件 diff。 |
| `get-content` | `GET /changes/{change-id}/revisions/{revision-id}/files/{file-id}/content` | 获取文件内容。 |
| `get-mergeable` | `GET /changes/{change-id}/revisions/{revision-id}/mergeable` | 获取 mergeable 状态。 |
| `submitted-together` | `GET /changes/{change-id}/submitted_together` | 获取联动提交 changes。 |
| `list-projects` | `GET /projects/` | 列出项目。 |
| `get-project` | `GET /projects/{project-name}` | 获取项目。 |
| `list-branches` | `GET /projects/{project-name}/branches/` | 列出分支。 |
| `list-tags` | `GET /projects/{project-name}/tags/` | 列出标签。 |
| `get-project-config` | `GET /projects/{project-name}/config` | 获取项目配置。 |
| `list-labels` | `GET /projects/{project-name}/labels/` | 获取项目 labels。 |
| `list-submit-requirements` | `GET /projects/{project-name}/submit_requirements` | 获取项目 submit requirements。 |

建议 CLI 示例：

```bash
python scripts/gerrit_cli.py query-changes \
  --query "reviewer:self -owner:self status:open" \
  --option CURRENT_REVISION \
  --option DETAILED_ACCOUNTS \
  --option DETAILED_LABELS \
  --limit 25
```

```bash
python scripts/gerrit_cli.py get-diff \
  --change "myProject~4247" \
  --revision current \
  --file "src/main/App.java" \
  --context 50 \
  --intraline
```

### 6.2 第二优先级：评审写操作

这些请求是 Agent Code Review 的核心，但属于写操作。应脚本化，同时加保护。

| 脚本命令 | REST API | 风险等级 | 保护策略 |
|---|---|---|---|
| `review` | `POST /changes/{change-id}/revisions/{revision-id}/review` | 中 | 输出 review 摘要，支持 `--dry-run`。 |
| `vote` | 同 `review`，只传 `labels` | 中 | 显示 labels 变化，要求明确 label/value。 |
| `comment` | 同 `review`，只传 `comments` | 中 | 校验 file path、line、unresolved。 |
| `publish-drafts` | 同 `review`，`drafts=PUBLISH` | 中 | 默认不发布，必须显式传参。 |
| `add-reviewer` | `POST /changes/{change-id}/reviewers` | 低到中 | 默认 `notify=OWNER_REVIEWERS`，支持 `--state CC`。 |
| `remove-reviewer` | `DELETE /changes/{change-id}/reviewers/{account-id}` | 中 | 展示 reviewer identity 后执行。 |
| `delete-vote` | `DELETE /changes/{change-id}/reviewers/{account-id}/votes/{label-id}` | 中 | 展示 label/account。 |
| `set-topic` | `PUT /changes/{change-id}/topic` | 低 | 支持空 topic 删除。 |
| `set-hashtags` | `POST /changes/{change-id}/hashtags` | 低 | 显示 add/remove 列表。 |
| `set-wip` | `POST /changes/{change-id}/wip` | 中 | 要求 message 或 reason。 |
| `set-ready` | `POST /changes/{change-id}/ready` | 中 | 要求 message 或 reason。 |
| `attention-add` | `POST /changes/{change-id}/attention` | 低 | 要求 account 和 reason。 |
| `attention-remove` | `DELETE /changes/{change-id}/attention/{account-id}` | 低 | 要求 account 和 reason。 |

建议 `review` 支持输入 JSON 文件：

```bash
python scripts/gerrit_cli.py review \
  --change "myProject~4247" \
  --revision current \
  --input review.json \
  --notify OWNER_REVIEWERS
```

`review.json`：

```json
{
  "message": "Reviewed by agent.",
  "labels": {
    "Code-Review": 1
  },
  "comments": {
    "src/main/App.java": [
      {
        "line": 42,
        "message": "建议补充边界条件处理。",
        "unresolved": true
      }
    ]
  },
  "tag": "autogenerated:active-gerrit"
}
```

### 6.3 第三优先级：Change 动作

这些动作会改变 change 状态，必须脚本化但带强约束。

| 脚本命令 | REST API | 风险等级 | 执行前检查 |
|---|---|---|---|
| `submit` | `POST /changes/{change-id}/submit` | 高 | 刷新 detail、submit requirements、mergeable、submitted together。 |
| `abandon` | `POST /changes/{change-id}/abandon` | 高 | 要求 message，展示 owner/project/branch/status。 |
| `restore` | `POST /changes/{change-id}/restore` | 高 | 要求 message。 |
| `rebase` | `POST /changes/{change-id}/rebase` | 高 | 展示 base，默认 `allow_conflicts=false`。 |
| `rebase-chain` | `POST /changes/{change-id}/rebase:chain` | 高 | 展示链路范围。 |
| `move` | `POST /changes/{change-id}/move` | 高 | 展示目标 branch。 |
| `revert` | `POST /changes/{change-id}/revert` | 高 | 展示将创建的新 change。 |
| `revert-submission` | `POST /changes/{change-id}/revert_submission` | 高 | 展示将影响的 changes。 |
| `cherrypick-revision` | `POST /changes/{change-id}/revisions/{revision-id}/cherrypick` | 高 | 要求 destination 和 message。 |

建议所有高风险命令默认行为：

```text
默认 dry-run，只输出计划。
用户显式传 --yes 才执行。
执行前强制重新读取 change detail。
执行后输出 updated refs、change status、后续建议。
```

### 6.4 第四优先级：Change Edit

Change Edit 适合 Agent 做小修复，但需要严格保护，避免覆盖用户改动。

| 脚本命令 | REST API | 说明 |
|---|---|---|
| `edit-get` | `GET /changes/{change-id}/edit` | 获取 edit 状态。 |
| `edit-put-file` | `PUT /changes/{change-id}/edit/{file-id}` | 写入文件内容。 |
| `edit-delete-file` | `DELETE /changes/{change-id}/edit/{file-id}` | 删除文件。 |
| `edit-message` | `PUT /changes/{change-id}/edit:message` | 修改 commit message。 |
| `edit-publish` | `POST /changes/{change-id}/edit:publish` | 发布新 patch set。 |
| `edit-rebase` | `POST /changes/{change-id}/edit:rebase` | rebase edit。 |
| `edit-delete` | `DELETE /changes/{change-id}/edit` | 删除 edit。 |

保护策略：

- 写文件前先读取 current revision 的文件内容或 diff。
- 默认只允许修改用户明确指定的文件。
- `edit-publish` 前展示文件列表和 commit message。
- 如果已有 change edit，先展示 edit owner/base patch set，避免误覆盖。

### 6.5 第五优先级：项目、权限、管理员接口

这些 API 可以脚本化，但不应放在第一阶段默认工作流里。

| 脚本命令 | REST API | 建议 |
|---|---|---|
| `create-project` | `PUT /projects/{project-name}` | 管理员/项目管理员能力，默认 dry-run。 |
| `create-branch` | `PUT /projects/{project-name}/branches/{branch-id}` | 可脚本化。 |
| `delete-branch` | `DELETE /projects/{project-name}/branches/{branch-id}` | 高风险，默认 dry-run。 |
| `create-tag` | `PUT /projects/{project-name}/tags/{tag-id}` | 可脚本化。 |
| `delete-tag` | `DELETE /projects/{project-name}/tags/{tag-id}` | 高风险，默认 dry-run。 |
| `get-access` | `GET /projects/{project-name}/access` | 可脚本化。 |
| `access-review` | `PUT /projects/{project-name}/access:review` | 推荐优先走 review，而不是直接修改。 |
| `update-access` | `POST /projects/{project-name}/access` | 高风险，默认不开放或仅管理员模式。 |
| `labels-review` | `POST /projects/{project-name}/labels:review` | 推荐走 review。 |
| `submit-requirements-review` | `POST /projects/{project-name}/submit_requirements:review` | 推荐走 review。 |
| `flush-cache` | `POST /config/server/caches/{cache-name}/flush` | 管理员高风险。 |
| `reindex` | `POST /config/server/indexes/.../reindex` | 管理员高风险。 |
| `plugin-enable/disable/reload` | `/plugins/...` | 管理员高风险。 |

### 6.6 `active-gerrit-workflow` 适合固化的流程脚本

这些脚本不直接表达 Gerrit endpoint，而是表达业务动作。它们应通过 `active-gerrit` 获取数据和执行写操作。

| 流程脚本命令 | 依赖的 `active-gerrit` 能力 | 说明 |
|---|---|---|
| `workflow my-review-queue` | `query-preset my_open_reviews`、`get-change` | 获取待我评审列表，按业务风险、更新时间、owner、分支排序。 |
| `workflow review-brief` | `get-change`、`list-files`、`get-diff`、`list-comments` | 为一个 change 生成评审摘要、风险点和建议关注文件。 |
| `workflow pre-submit-check` | `get-change`、`get-mergeable`、`submitted-together` | 按团队规则检查是否可以 submit。 |
| `workflow release-branch-check` | `query-changes`、`list-branches`、`get-change` | 检查 release 分支待合入、阻塞项和风险 change。 |
| `workflow owner-report` | `query-changes`、`get-change` | 按 owner 汇总 open changes、超时 changes、缺 reviewer changes。 |
| `workflow stale-review-report` | `query-changes`、`list-comments`、`list-messages` | 找出长时间无人响应的 review。 |
| `workflow add-standard-reviewers` | `add-reviewer`、`query-accounts` | 按团队规则添加默认 reviewers/CC。 |
| `workflow post-review-summary` | `review` | 将流程生成的评审摘要发布为 patchset-level comment。 |
| `workflow batch-ready-check` | `query-changes`、`get-change`、`get-mergeable` | 批量检查多个 changes 是否 ready。 |
| `workflow config-change-review` | `get-change`、`get-diff` | 对 `refs/meta/config` 变更按权限/label/SR 规则做专项检查。 |

流程脚本的保护策略：

- 默认只生成报告，不直接执行写操作。
- 需要发表评论、加 reviewer、submit 等动作时，调用 `active-gerrit` 的对应命令，并继承其 dry-run / `--yes` 机制。
- 流程脚本输出应包含 `used_active_gerrit_commands`，便于审计调用链。
- 业务规则命中但证据不足时，输出 `needs_human_decision`，不做自动结论。

## 7. 不建议完全固化的部分

以下内容不应被做成“无脑脚本”，而应由 Agent 读取上下文后决策，再调用脚本执行：

| 类型 | 原因 | 建议 |
|---|---|---|
| Review 结论 | 需要理解代码和业务语义。 | Agent 生成 message，脚本只负责提交。 |
| Inline comment 文案 | 需要结合 diff 和上下文。 | 脚本校验行号和格式，不生成结论。 |
| 是否给 `Code-Review +2` | 权限和团队规范差异大。 | 默认不自动 +2，除非用户明确要求。 |
| 是否 submit | 高风险动作。 | 必须刷新状态并展示摘要。 |
| Access rule 具体权限设计 | 容易影响仓库安全。 | 用 review change 承载，人工审核。 |
| 批量删除 branch/tag | 破坏性强。 | 强制 dry-run 和显式确认。 |
| 插件启停、cache/index 管理 | 影响全局服务。 | 管理员专用模式。 |

## 8. 可以固化的查询结果

“固化”分为两层：

1. 标准化输出结构：即使不缓存，也让 Agent 得到稳定 JSON schema。
2. 本地缓存：对低变化或不可变结果写入 `.cache/gerrit/`，减少重复请求。

`active-gerrit` 负责固化 Gerrit 原子查询结果，例如 `ChangeSummary`、`ChangeDetail`、`FileDiff`。

`active-gerrit-workflow` 负责固化业务聚合结果，例如“待评审队列报告”“发布前检查报告”“owner 维度积压报告”。这些聚合结果应引用底层 Gerrit 原子结果的 id、revision、fetched_at，而不是复制大量 raw data。

### 8.1 缓存总原则

- 不缓存密码、Authorization header、cookie、token。
- 不默认缓存完整源码文件内容；如需缓存，必须明确开启。
- `current` revision 是移动引用，不应长期缓存；先解析成具体 patch set number 或 commit SHA 后再缓存。
- 写操作前必须绕过缓存刷新关键状态。
- 缓存文件不应提交到 Git，后续应加入 `.gitignore`。

建议缓存目录：

```text
.cache/gerrit/
├── server/
├── accounts/
├── projects/
├── changes/
├── revisions/
└── operations.jsonl
```

### 8.2 强烈建议固化的结果

| 结果 | 固化方式 | 缓存 Key | TTL 建议 | 说明 |
|---|---|---|---|---|
| Gerrit version | 标准化 + 缓存 | `server/version` | 1 天或进程级 | 低变化，用于兼容性判断。 |
| Server info | 标准化 + 缓存 | `server/info` | 1 小时 | 下载协议、auth、change 配置等。 |
| 当前账号 | 标准化 + 缓存 | `accounts/self` | 1 小时 | 高频使用。 |
| 当前账号 capabilities | 标准化 + 缓存 | `accounts/self/capabilities` | 10 分钟 | 权限可能变化，不宜太久。 |
| Account 解析 | 标准化 + 缓存 | `accounts/resolve/<query>` | 1 小时 | 用户名/邮箱到 `_account_id`。 |
| Project 列表 | 标准化 + 缓存 | `projects/list/<query>` | 10 分钟 | 项目变化频率中低。 |
| Project 基础信息 | 标准化 + 缓存 | `projects/<project>` | 10 分钟 | 常用。 |
| Branch 列表 | 标准化 + 缓存 | `projects/<project>/branches` | 5 到 10 分钟 | 分支可能新增。 |
| Tag 列表 | 标准化 + 缓存 | `projects/<project>/tags` | 10 分钟 | 标签变化频率中低。 |
| Project labels | 标准化 + 缓存 | `projects/<project>/labels` | 10 分钟 | 用于判断可投票 label。 |
| Submit requirement 定义 | 标准化 + 缓存 | `projects/<project>/submit_requirements` | 10 分钟 | 项目配置变化后刷新。 |
| Change ID 映射 | 标准化 + 缓存 | `changes/map/<input>` | 10 分钟 | 将 Change-Id/number 解析为 `<project>~<number>`。 |

### 8.3 可缓存但必须谨慎的结果

| 结果 | 固化方式 | 缓存 Key | TTL 建议 | 注意事项 |
|---|---|---|---|---|
| Change summary | 标准化 + 短缓存 | `changes/query/<hash>` | 30 到 60 秒 | 状态、label、reviewer 都会变化。 |
| Change detail | 标准化 + 短缓存 | `changes/<project~number>/detail` | 15 到 30 秒 | 写操作前必须刷新。 |
| Reviewers | 标准化 + 短缓存 | `changes/<id>/reviewers` | 30 秒 | 变化较频繁。 |
| Comments | 标准化 + 短缓存 | `changes/<id>/comments` | 30 秒 | 评审中变化频繁。 |
| Messages | 标准化 + 短缓存 | `changes/<id>/messages` | 30 秒 | 写操作后立即过期。 |
| Attention set | 标准化 + 短缓存 | `changes/<id>/attention` | 30 秒 | 协作状态变化频繁。 |
| Mergeable | 标准化，不建议持久缓存 | `changes/<id>/mergeable` | 5 到 15 秒 | submit 前必须刷新。 |
| Submitted together | 标准化，不建议持久缓存 | `changes/<id>/submitted_together` | 5 到 15 秒 | submit 前必须刷新。 |
| Submit readiness | 标准化，不建议持久缓存 | `changes/<id>/submit_check` | 5 到 15 秒 | 高风险决策依据。 |

### 8.4 适合长缓存的不可变 revision 结果

当 `revision-id` 已解析为具体 patch set number 或 commit SHA 时，以下结果近似不可变，可长缓存：

| 结果 | 缓存 Key | TTL 建议 | 说明 |
|---|---|---|---|
| Revision commit | `revisions/<change>/<revision>/commit` | 7 天 | patch set 不会被修改。 |
| Revision file list | `revisions/<change>/<revision>/files` | 7 天 | 同一 patch set 文件列表稳定。 |
| File diff | `revisions/<change>/<base>..<revision>/<file>/<options>` | 7 天 | diff options 必须进入 key。 |
| Patch text | `revisions/<change>/<revision>/patch` | 7 天 | patch set 固定。 |
| Blame | `revisions/<change>/<revision>/<file>/blame` | 7 天 | 计算开销较大，可缓存。 |

注意：

- 如果用户传 `current`，脚本应先获取当前 patch set number 或 SHA，再使用解析后的 revision 建缓存 key。
- 如果 change 发布了新 patch set，`current` 对应缓存必须失效。

### 8.5 不建议缓存的结果

| 结果 | 原因 |
|---|---|
| HTTP password、token、cookie | 敏感凭据。 |
| Authorization header | 敏感凭据。 |
| Raw request/response headers 全量 | 可能包含敏感信息。 |
| submit/rebase/abandon 的执行前状态 | 写操作必须实时刷新。 |
| 用户未明确允许的完整源码文件内容 | 可能涉及代码保密。 |
| 删除/权限修改类 dry-run 计划之外的实际状态 | 容易误导后续操作。 |

## 9. 标准化查询结果 Schema

### 9.1 `ChangeSummary`

`query-changes` 输出时建议只保留 Agent 高频使用字段：

```json
{
  "id": "myProject~4247",
  "triplet_id": "myProject~master~I...",
  "number": 4247,
  "project": "myProject",
  "branch": "master",
  "change_id": "I...",
  "subject": "Fix bug",
  "status": "NEW",
  "owner": {
    "account_id": 1000001,
    "name": "Alice",
    "email": "alice@example.com",
    "username": "alice"
  },
  "updated": "2026-05-08 10:00:00.000000000",
  "current_revision": "abc123...",
  "current_patch_set": 3,
  "labels": {},
  "submit_requirements": [],
  "unresolved_comment_count": 2,
  "hashtags": [],
  "topic": "feature-x"
}
```

### 9.2 `ChangeDetail`

`get-change` 输出：

```json
{
  "summary": {},
  "revisions": [
    {
      "revision": "abc123...",
      "patch_set": 3,
      "created": "2026-05-08 10:00:00.000000000",
      "uploader": {},
      "ref": "refs/changes/47/4247/3",
      "files_count": 12,
      "fetch": {}
    }
  ],
  "reviewers": {
    "REVIEWER": [],
    "CC": [],
    "REMOVED": []
  },
  "messages": [],
  "actions": {},
  "raw": {}
}
```

`raw` 可以通过 `--include-raw` 开启，默认不输出，减少上下文负担。

### 9.3 `FileDiff`

`get-diff` 输出：

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

### 9.4 `ReviewPlan`

写 review 前可先生成计划：

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

### 9.5 `WorkflowReport`

`active-gerrit-workflow` 输出的业务聚合报告建议统一为：

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
      "evidence": ["Code-Review is missing"]
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

## 10. 可以固化的查询模板

这些是 Agent 高频查询，可以在脚本中作为 preset 固化。

| Preset | Gerrit query | 用途 |
|---|---|---|
| `my_open_reviews` | `reviewer:self -owner:self status:open` | 待我评审。 |
| `my_owned_open` | `owner:self status:open` | 我创建的 open changes。 |
| `project_open` | `project:{project} status:open` | 项目 open changes。 |
| `project_branch_open` | `project:{project} branch:{branch} status:open` | 项目分支 open changes。 |
| `ready_to_submit` | `status:open is:submittable` | 可提交 changes。 |
| `needs_review` | `status:open -is:wip` | 需要评审的 changes。 |
| `wip` | `status:open is:wip` | WIP changes。 |
| `recent_merged` | `status:merged after:{date}` | 最近合入。 |
| `by_change_number` | `change:{number}` | 按 change number 查询。 |
| `by_change_id` | `{Change-Id}` | 按 Change-Id 查询。 |

脚本接口：

```bash
python scripts/gerrit_cli.py query-preset my_open_reviews --limit 25
python scripts/gerrit_cli.py query-preset project_branch_open --project myProject --branch master
```

Preset 输出仍统一为 `ChangeSummary[]`。

## 11. 写操作安全策略

### 11.1 风险分级

| 风险 | 操作 | 默认行为 |
|---|---|---|
| 低 | 查询、读取 diff、列文件、列评论 | 直接执行。 |
| 中 | 发评论、投票、加 reviewer、改 topic、改 WIP | 可直接执行，但输出操作结果。 |
| 高 | submit、abandon、restore、rebase、move、revert、删除 reviewer/vote | 默认 dry-run，需 `--yes`。 |
| 管理员 | access、labels、submit requirements、cache、index、plugins、删除分支/标签 | 默认 dry-run，建议只生成 review change。 |

### 11.2 Submit 前检查

`submit` 命令必须执行：

```text
1. GET /changes/{change-id}/detail?o=DETAILED_LABELS&o=SUBMIT_REQUIREMENTS&o=CURRENT_REVISION&o=CURRENT_ACTIONS&o=SUBMITTABLE
2. GET /changes/{change-id}/revisions/current/mergeable
3. GET /changes/{change-id}/submitted_together
4. 检查 status == NEW
5. 检查 submittable 或 submit action 可用
6. 展示 submitted together 列表
7. 用户显式 --yes 后 POST /changes/{change-id}/submit
```

### 11.3 通知策略

默认通知：

| 操作 | 默认 notify |
|---|---|
| review/comment/vote | `OWNER_REVIEWERS` |
| add reviewer | `OWNER_REVIEWERS` |
| WIP/ready | `OWNER_REVIEWERS` |
| abandon/restore | `OWNER` |
| submit | `ALL` |

允许用户覆盖：

```bash
--notify NONE|OWNER|OWNER_REVIEWERS|ALL
```

## 12. CLI 命令清单建议

第一阶段最小可用：

```text
doctor
version
whoami
query-changes
query-preset
get-change
list-files
get-diff
list-comments
review
add-reviewer
set-wip
set-ready
submit
abandon
rebase
list-projects
list-branches
```

第二阶段：

```text
get-content
list-drafts
publish-drafts
remove-reviewer
delete-vote
set-topic
set-hashtags
attention-add
attention-remove
submitted-together
get-mergeable
cherrypick-revision
revert
restore
edit-get
edit-put-file
edit-publish
```

第三阶段：

```text
get-project-config
list-labels
list-submit-requirements
access-review
labels-review
submit-requirements-review
create-branch
create-tag
```

管理员扩展：

```text
flush-cache
reindex
list-tasks
delete-task
plugin-list
plugin-enable
plugin-disable
plugin-reload
```

## 13. 实施路线图

### 阶段 0：整理双 Skill 资源

- 将 `doc/Gerrit REST API.md` 精简迁移到 `active-gerrit/references/gerrit-rest-api-3.11.2.md`。
- 新增 `active-gerrit/SKILL.md`。
- 新增 `active-gerrit/agents/openai.yaml`。
- 新增 `active-gerrit-workflow/SKILL.md`。
- 新增 `active-gerrit-workflow/agents/openai.yaml`。
- 新增 `active-gerrit-workflow/references/business-workflows.md`，先放最小业务流程占位和引用规则。

验收：

- `active-gerrit` metadata 能准确触发 Gerrit 基础任务。
- `active-gerrit-workflow` metadata 能准确触发业务流程任务。
- 两个 `SKILL.md` 都不超过 500 行。
- `active-gerrit-workflow` 明确说明需要底层 Gerrit 操作时优先使用 `active-gerrit`。

### 阶段 1：实现 Basic Auth Client

- 实现 `gerrit_client.py`。
- 支持 Basic Auth。
- 支持 XSSI 清理。
- 支持重复 query 参数。
- 支持错误 envelope。
- 实现 `doctor`、`version`、`whoami`。
- `doctor` 必须包含系统命令、Python 版本、环境变量、Gerrit 连通性、鉴权、缓存目录的前置检查。

验收：

```bash
python scripts/gerrit_cli.py doctor
python scripts/gerrit_cli.py whoami
```

### 阶段 2：实现只读 Code Review 能力

- `query-changes`
- `query-preset`
- `get-change`
- `list-files`
- `get-diff`
- `list-comments`
- `list-reviewers`

验收：

- 能读取待我评审 changes。
- 能获取某个 change 的 current patch set diff。
- 输出为稳定 JSON schema。

### 阶段 3：实现评审写操作

- `review`
- `vote`
- `comment`
- `add-reviewer`
- `set-wip`
- `set-ready`

验收：

- 能发布 patchset-level comment。
- 能发布 inline comment。
- 能设置 `Code-Review` vote。
- 能添加 reviewer/CC。

### 阶段 4：实现高风险 Change 动作

- `submit`
- `abandon`
- `restore`
- `rebase`
- `revert`
- `cherrypick-revision`

验收：

- 默认 dry-run。
- `submit` 前自动刷新 readiness。
- 执行后输出状态变化。

### 阶段 5：实现缓存与固化结果

- 实现 `.cache/gerrit`。
- 对 version、whoami、account resolve、project、branch、revision diff 做缓存。
- 对 change detail、mergeable、submit readiness 做短缓存或强制刷新。

验收：

- 缓存不包含凭据。
- 写操作前绕过关键状态缓存。
- 支持 `--no-cache` 和 `--refresh`。

### 阶段 6：实现业务流程 Skill

- 实现 `active-gerrit-workflow/scripts/workflow_cli.py`。
- 固化 `my-review-queue`、`review-brief`、`pre-submit-check` 三个最小业务流程。
- 流程脚本调用 `active-gerrit/scripts/gerrit_cli.py`，不重复实现 REST client。
- 输出 `WorkflowReport`。

验收：

- 能基于 `active-gerrit` 输出生成待评审队列报告。
- 能对单个 change 生成评审摘要。
- 能执行 submit 前业务检查，但默认不 submit。
- 流程报告包含 `used_active_gerrit_commands`。

## 14. 最小脚本接口草案

`active-gerrit` 基础脚本：

```bash
python scripts/gerrit_cli.py doctor
python scripts/gerrit_cli.py query-preset my_open_reviews --limit 25
python scripts/gerrit_cli.py get-change --change myProject~4247 --detail full
python scripts/gerrit_cli.py list-files --change myProject~4247 --revision current
python scripts/gerrit_cli.py get-diff --change myProject~4247 --revision current --file src/main/App.java
python scripts/gerrit_cli.py review --change myProject~4247 --revision current --input review.json
python scripts/gerrit_cli.py add-reviewer --change myProject~4247 --reviewer alice@example.com
python scripts/gerrit_cli.py submit --change myProject~4247 --dry-run
python scripts/gerrit_cli.py submit --change myProject~4247 --yes
```

`active-gerrit-workflow` 流程脚本：

```bash
python scripts/workflow_cli.py my-review-queue --limit 25
python scripts/workflow_cli.py review-brief --change myProject~4247
python scripts/workflow_cli.py pre-submit-check --change myProject~4247
python scripts/workflow_cli.py owner-report --project myProject --after 2026-05-01
python scripts/workflow_cli.py stale-review-report --project myProject --days 7
```

## 15. 最终交付物

建议最终交付：

```text
active-gerrit/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── gerrit-rest-api-3.11.2.md
│   ├── core-workflows.md
│   └── result-schemas.md
└── scripts/
    ├── gerrit_client.py
    ├── gerrit_cli.py
    ├── gerrit_cache.py
    └── gerrit_errors.py

active-gerrit-workflow/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── business-workflows.md
│   ├── review-policies.md
│   ├── release-policies.md
│   └── escalation-rules.md
└── scripts/
    ├── workflow_cli.py
    ├── workflow_rules.py
    └── workflow_reports.py
```

仓库级文档继续保留：

```text
README.md
doc/
├── Gerrit REST API.md
└── Gerrit Skill 封装方案.md
```

## 16. 一句话方案

用 Basic Auth 作为默认鉴权，将 Gerrit 能力拆成两层：`active-gerrit` 固化 REST API 基础能力并作为 fallback，`active-gerrit-workflow` 固化业务流程编排并复用基础层；让 Agent 负责理解代码和业务决策，让脚本负责可靠调用 Gerrit、校验参数、处理错误、固化结果和保护写操作。

## 17. 本地 Git 命令封装方案

> 本节追加于 `2026-05-11`，用于补齐 `active-gerrit` 中“Gerrit REST + 本地 Git”混合工作流能力。
>
> 调研基线：
>
> - 本机 `git version 2.34.1`。
> - 调研时当前仓库为 `main...origin/main`，追加文档前本地工作区干净。
> - 已通过 `git status -h`、`git fetch -h`、`git push -h`、`git diff -h`、`git commit -h`、`git branch -h`、`git remote -h`、`git worktree -h`、`git cherry-pick -h` 梳理可机器解析选项和高风险选项。

### 17.1 为什么需要封装本地 Git

Gerrit REST API 适合查询、评审、投票、submit、change edit 等操作；但以下场景仍然需要本地 Git：

| 场景 | REST 能力 | 本地 Git 能力 |
|---|---|---|
| 拉取 patch set 本地复现 | `ChangeInfo.revisions[].ref/fetch` 提供 ref 与命令 | `git fetch`、`git switch`、`git worktree add`。 |
| 代码修改后上传新 patch set | REST Change Edit 可做小改动 | `git add`、`git commit --amend`、`git push HEAD:refs/for/<branch>` 更符合开发流程。 |
| 本地状态安全检查 | REST 不知道工作区状态 | `git status --porcelain`、`git diff --name-status`、`git rev-parse`。 |
| 大范围代码评审和测试 | REST diff 足够阅读 | 本地 checkout 后可运行测试、搜索、静态分析。 |
| Change-Id 管理 | REST 可读 commit message | 本地 commit message、hook、trailer 检查更可靠。 |
| 分支和远端诊断 | REST 可查 project/branch | `git remote`、`git ls-remote`、`git rev-list` 可验证本地仓库是否对准 Gerrit。 |

结论：`active-gerrit` 应保留 REST 作为基础事实来源，同时增加本地 Git 封装，专门处理 repo 识别、工作区安全、patch set fetch/checkout、commit/amend、review push 和本地 diff/log 输出。

### 17.2 分层边界

建议在 `active-gerrit` 中新增本地 Git 子模块，仍由基础 Skill 统一负责；`active-gerrit-workflow` 只编排，不直接拼 git 命令。

```text
active-gerrit/
└── scripts/
    ├── gerrit_cli.py          # 现有 Gerrit REST CLI
    ├── gerrit_client.py
    ├── gerrit_cache.py
    ├── gerrit_errors.py
    ├── git_cli.py             # 新增：本地 Git CLI 入口
    ├── git_runner.py          # 新增：安全执行 git 子进程
    ├── git_schemas.py         # 新增：Git 输出标准化
    └── git_gerrit.py          # 新增：Gerrit ref、refs/for push、Change-Id 辅助
```

设计取舍：

- `gerrit_cli.py` 继续专注 REST，避免单文件继续膨胀。
- `git_cli.py` 使用和 `gerrit_cli.py` 一致的 JSON envelope，便于 workflow 层统一消费。
- `git_runner.py` 只能通过参数数组调用 `subprocess.run`，禁止 `shell=True`。
- 所有命令默认在 `--repo` 指定目录或当前目录向上找到的 Git 根目录执行。
- 本地 Git 不保存 Gerrit 密码，不读取 Authorization header，不把远端 URL 中可能存在的凭据原样输出。

### 17.3 GitRunner 安全执行规则

所有 Git 命令必须经过 `GitRunner`：

| 规则 | 说明 |
|---|---|
| 固定可执行文件 | 默认 `git`，可通过 `GIT_BIN` 覆盖，但 `doctor` 要显示实际路径。 |
| 禁止 shell 拼接 | 使用参数数组，例如 `["git", "status", "--porcelain=v1", "-z"]`。 |
| 受控工作目录 | 先用 `git rev-parse --show-toplevel` 找 repo root，再执行后续命令。 |
| 超时控制 | 默认 `GIT_TIMEOUT_SECONDS=60`，fetch/push 可单独配置更长。 |
| 输出截断 | stdout/stderr 设上限，超限时保留摘要和截断提示。 |
| 凭据脱敏 | remote URL、stderr、push 输出中脱敏 username/password/token。 |
| 机器可解析优先 | 优先使用 `--porcelain`、`-z`、`--format`、`--name-status`、`--numstat`。 |
| 高风险保护 | reset/clean/branch delete/force push 默认不开放；如开放必须 dry-run + `--yes`。 |

建议错误类型：

| Type | 触发场景 |
|---|---|
| `GitConfigError` | 不在 Git 仓库、缺少 remote、配置不完整。 |
| `GitDirtyWorktree` | 操作要求干净工作区但存在未提交改动。 |
| `GitCommandError` | git 子命令返回非 0。 |
| `GitConflict` | cherry-pick/rebase/merge 产生冲突。 |
| `GitAuthError` | fetch/push 认证失败。 |
| `GitRemoteError` | 远端不可达、ref 不存在、push 被拒绝。 |
| `GitValidationError` | 参数、branch、refspec、Change-Id 校验失败。 |

### 17.4 建议封装的本地 Git 命令

#### 17.4.1 基础诊断与仓库识别

| CLI 命令 | 底层 Git | 说明 |
|---|---|---|
| `git-doctor` | `git --version`、`git config`、`git rev-parse` | 检查 git、repo、user、remote、hooks。 |
| `repo-info` | `rev-parse --show-toplevel`、`branch --show-current`、`remote -v` | 输出当前仓库、分支、upstream、remote。 |
| `repo-status` | `status --porcelain=v1 --branch -z` | 输出工作区、暂存区、ahead/behind。 |
| `repo-remotes` | `remote -v`、`remote get-url` | 输出脱敏后的 fetch/push URL。 |
| `repo-config` | `config --get user.name` 等 | 只读输出与 Gerrit 工作流有关的配置。 |

示例：

```bash
python scripts/git_cli.py repo-status --repo /path/to/repo
```

#### 17.4.2 本地变更读取

| CLI 命令 | 底层 Git | 说明 |
|---|---|---|
| `repo-diff` | `diff --name-status`、`diff --numstat`、`diff --stat` | 默认只输出摘要；按需输出 patch。 |
| `repo-diff-file` | `diff -- <file>` | 读取单文件 diff，支持 staged/unstaged/base。 |
| `repo-log` | `log --format=...` | 输出近期 commit 摘要。 |
| `repo-show` | `show --stat --format=...` | 查看单个 commit。 |
| `repo-branches` | `branch --format=...` | 列出本地/远端分支。 |

机器解析建议：

```bash
git diff --name-status -z
git diff --numstat -z
git status --porcelain=v1 --branch -z
git log --format=%H%x00%h%x00%an%x00%ae%x00%ad%x00%s%x00 --date=iso-strict
git branch --format=%(refname)%00%(objectname)%00%(upstream:short)%00%(HEAD)%00%(subject)
```

#### 17.4.3 Gerrit patch set 获取与检出

| CLI 命令 | 依赖 | 底层 Git | 说明 |
|---|---|---|---|
| `fetch-change` | REST `get-change` | `git fetch <remote> <ref>` | 拉取指定 change revision。 |
| `checkout-change` | `fetch-change` | `git switch -c` 或 detached checkout | 检出 patch set 到分支。 |
| `worktree-change` | `fetch-change` | `git worktree add` | 为 change 创建独立 worktree。 |
| `cherry-pick-change` | `fetch-change` | `git cherry-pick` | 把 patch set 应用到当前分支。 |

优先使用 REST 返回的 `RevisionInfo.ref`，不要手动猜 ref；只有用户明确提供 change number 和 patch set 且 REST 不可用时，才按 Gerrit 规则构造：

```text
refs/changes/<change_number_last_two_digits>/<change_number>/<patch_set>
```

示例：

```bash
python scripts/git_cli.py fetch-change \
  --repo /path/to/repo \
  --remote origin \
  --change myProject~4247 \
  --revision current
```

#### 17.4.4 Commit 与 Change-Id

| CLI 命令 | 底层 Git | 说明 |
|---|---|---|
| `change-id-check` | `log -1 --format=%B`、`interpret-trailers` 可选 | 检查 HEAD 或消息文件是否含 `Change-Id`。 |
| `commit-plan` | `status`、`diff --cached` | 生成将要提交的文件和 message 摘要。 |
| `commit-create` | `add -- <paths>`、`commit -F <file>` | 创建新 commit，只允许显式 path。 |
| `commit-amend` | `commit --amend -F <file>` | 更新当前 change，默认要求保留 Change-Id。 |

约束：

- 默认不使用 `git commit -a`，避免意外提交用户未指定文件。
- commit message 推荐通过临时文件传入，避免复杂 quoting 和 shell history。
- 更新 Gerrit change 时，`commit-amend` 必须校验新旧 `Change-Id` 一致，除非用户明确要求创建新 change。
- 如果仓库缺少 `commit-msg` hook，`git-doctor` 应提示如何安装，但不要自动联网安装。

#### 17.4.5 Push Review

Gerrit review push 的核心形式：

```bash
git push <remote> HEAD:refs/for/<branch>
```

可封装为：

| CLI 命令 | 底层 Git | 说明 |
|---|---|---|
| `push-review-plan` | `status`、`log -1`、`rev-list`、`ls-remote` | 生成 push 计划，不执行。 |
| `push-review` | `push --porcelain` | 上传新 change 或新 patch set。 |

保护策略：

- 默认 dry-run，先执行 `git push --dry-run --porcelain` 或只生成计划。
- 必须要求工作区干净，除非用户显式允许 dirty push。
- 必须展示 `remote`、`branch`、`HEAD sha`、`subject`、`Change-Id`、目标 refspec。
- 支持 `--topic`、`--reviewer`、`--cc`、`--hashtag`、`--wip`、`--ready` 等 Gerrit ref options；需要 URL/ref 安全编码。
- `--force` 默认禁止；如未来支持，只允许 `--force-with-lease` 并要求 `--yes`。

示例：

```bash
python scripts/git_cli.py push-review \
  --repo /path/to/repo \
  --remote origin \
  --branch master \
  --topic feature-x \
  --reviewer alice@example.com \
  --dry-run
```

### 17.5 命令风险分级

| 风险 | 命令 | 默认行为 |
|---|---|---|
| 低 | `repo-info`、`repo-status`、`repo-remotes`、`repo-diff`、`repo-log` | 直接执行，只读。 |
| 中 | `fetch-change`、`checkout-change`、`worktree-change`、`commit-plan` | 执行前检查 repo 和 dirty 状态。 |
| 高 | `cherry-pick-change`、`commit-create`、`commit-amend`、`push-review` | 默认 dry-run 或生成计划；需要明确执行参数。 |
| 禁止默认开放 | `reset --hard`、`clean -fd`、`branch -D`、`push --force`、`remote set-url` | 不做 MVP；后续如做必须单独管理员/维护模式。 |

### 17.6 标准输出 Schema

本地 Git CLI 继续使用统一 envelope，`source` 建议为 `git`。

```json
{
  "ok": true,
  "command": "repo-status",
  "source": "git",
  "data": {},
  "warnings": [],
  "meta": {
    "repo": "/path/to/repo",
    "git_version": "2.34.1",
    "fetched_at": "2026-05-11T10:00:00+08:00"
  }
}
```

`GitRepoInfo`：

```json
{
  "repo_root": "/path/to/repo",
  "git_dir": "/path/to/repo/.git",
  "is_inside_work_tree": true,
  "current_branch": "feature-x",
  "head": "abc123",
  "upstream": "origin/master",
  "ahead": 1,
  "behind": 0,
  "remotes": [
    {
      "name": "origin",
      "fetch_url": "https://gerrit.example.com/project",
      "push_url": "https://gerrit.example.com/project"
    }
  ]
}
```

`GitStatus`：

```json
{
  "repo_root": "/path/to/repo",
  "branch": "feature-x",
  "upstream": "origin/master",
  "ahead": 1,
  "behind": 0,
  "is_clean": false,
  "staged": [],
  "unstaged": [],
  "untracked": [],
  "conflicts": [],
  "stash_count": 0
}
```

`GitDiffSummary`：

```json
{
  "base": "HEAD",
  "target": "working-tree",
  "staged": false,
  "files": [
    {
      "path": "src/main/App.java",
      "status": "M",
      "old_path": null,
      "insertions": 10,
      "deletions": 2
    }
  ],
  "stat": {
    "files_changed": 1,
    "insertions": 10,
    "deletions": 2
  },
  "patch": null
}
```

`GitChangeCheckout`：

```json
{
  "change": "myProject~4247",
  "revision": "current",
  "resolved_revision": "3",
  "remote": "origin",
  "ref": "refs/changes/47/4247/3",
  "fetched_commit": "abc123",
  "checkout_mode": "branch",
  "branch": "review/4247-3",
  "worktree": null
}
```

`GitPushReviewPlan`：

```json
{
  "repo_root": "/path/to/repo",
  "remote": "origin",
  "branch": "master",
  "head": "abc123",
  "subject": "Fix bug",
  "change_id": "Iabc123",
  "target_ref": "refs/for/master%topic=feature-x",
  "dry_run": true,
  "requires_clean_worktree": true,
  "warnings": []
}
```

### 17.7 Gerrit + Git 混合工作流

#### 17.7.1 本地拉取并评审一个 patch set

```text
1. active-gerrit: get-change --detail full，拿到 project、branch、current revision、ref。
2. git_cli: repo-info，确认当前 repo remote 与 Gerrit project 匹配。
3. git_cli: repo-status，确认是否允许 checkout 或建议使用 worktree。
4. git_cli: fetch-change，拉取 RevisionInfo.ref。
5. git_cli: checkout-change 或 worktree-change。
6. 本地运行测试/搜索/静态分析。
7. active-gerrit: review/comment/vote 发布评审结论。
```

#### 17.7.2 本地修复并上传新 patch set

```text
1. active-gerrit: get-change，确认目标 change 和 current patch set。
2. git_cli: fetch-change + checkout-change/worktree-change。
3. Agent 或用户修改文件。
4. git_cli: repo-status + repo-diff，生成提交计划。
5. git_cli: commit-amend，保留原 Change-Id。
6. git_cli: push-review-plan，展示 target refspec。
7. git_cli: push-review --yes，上传新 patch set。
8. active-gerrit: get-change --refresh，确认新 patch set 已出现。
9. active-gerrit: review，可选发布说明评论。
```

#### 17.7.3 从当前本地分支创建新 review

```text
1. git_cli: repo-info + repo-status。
2. git_cli: change-id-check，若缺失 Change-Id，提示安装 commit-msg hook 或要求用户确认创建新 Change-Id。
3. git_cli: push-review-plan，确认 branch、topic、reviewer、cc。
4. git_cli: push-review --yes。
5. active-gerrit: query-changes --query <Change-Id>，解析新 change number。
```

### 17.8 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `GIT_BIN` | `git` | Git 可执行文件路径。 |
| `GIT_TIMEOUT_SECONDS` | `60` | 普通 Git 命令超时。 |
| `GIT_FETCH_TIMEOUT_SECONDS` | `180` | fetch 超时。 |
| `GIT_PUSH_TIMEOUT_SECONDS` | `300` | push 超时。 |
| `GERRIT_GIT_REMOTE` | 空 | 默认 Gerrit remote 名；为空时自动选择 `origin` 或匹配 Gerrit URL 的 remote。 |
| `GERRIT_REVIEW_BRANCH` | 空 | 默认 review 目标分支；为空时使用当前 branch 的 upstream 或 change branch。 |
| `GIT_ALLOW_DIRTY_CHECKOUT` | `false` | 是否允许 dirty worktree 下 checkout；默认不允许。 |

### 17.9 测试策略

单元测试不应依赖真实 Gerrit 或真实远端仓库：

- 用临时目录 `git init` 构造本地仓库。
- 用本地 bare repo 模拟 remote，测试 fetch/push/refspec。
- 用固定 stdout 样本测试 `status --porcelain -z`、`diff --name-status -z`、`branch --format` parser。
- 用假 `git` 可执行文件测试错误、超时、stderr 脱敏。
- push review 默认只测 `--dry-run` 和 refspec 构造；真实 Gerrit push 放入集成测试。

### 17.10 MVP 建议

第一批优先实现：

```text
git-doctor
repo-info
repo-status
repo-remotes
repo-diff
change-id-check
fetch-change
checkout-change
push-review-plan
push-review --dry-run
```

第二批实现：

```text
worktree-change
repo-log
repo-show
repo-branches
commit-plan
commit-create
commit-amend
push-review --yes
```

第三批谨慎实现：

```text
cherry-pick-change
abort-cherry-pick
safe-clean-plan
branch-delete-plan
force-with-lease-push
```

本地 Git 封装的一句话目标：让 Agent 通过稳定 JSON 了解本地仓库状态，通过受保护命令完成 fetch/checkout/commit/push review，不直接临时拼接高风险 git 命令。
