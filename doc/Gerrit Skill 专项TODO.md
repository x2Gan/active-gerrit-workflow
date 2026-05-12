# Gerrit Skill 专项 TODO

> 目标：将 Gerrit Skill 分层封装方案拆解为可执行、可验收、可追踪的软件工程任务。
>
> 适用范围：
>
> - `active-gerrit`：Gerrit REST API 基础能力 Skill，也是 Gerrit 能力 fallback 兜底层。
> - `active-gerrit-workflow`：结合业务流程的高级 Skill，复用 `active-gerrit` 的能力。
>
> 关联文档：
>
> - [Gerrit Skill 封装方案.md](./Gerrit%20Skill%20封装方案.md)
> - [Gerrit REST API.md](./Gerrit%20REST%20API.md)
> - [install.sh 实现方案.md](./install.sh%20实现方案.md)

## 1. 任务管理约定

### 1.1 优先级

| 优先级 | 含义 |
|---|---|
| `P0` | MVP 必须完成；没有它无法形成可用 Skill。 |
| `P1` | 第一版建议完成；显著提升可用性和安全性。 |
| `P2` | 增强能力；可在基础稳定后迭代。 |
| `P3` | 长期优化或管理员扩展。 |

### 1.2 任务状态

| 状态 | 含义 |
|---|---|
| `[ ]` | 未开始。 |
| `[~]` | 进行中。 |
| `[x]` | 已完成。 |
| `[!]` | 阻塞。 |

### 1.3 交付定义

每个任务完成时应至少满足：

- 有明确文件产出或命令产出。
- 有基础验证命令或测试用例。
- 不泄露 Gerrit 密码、token、cookie、Authorization header。
- 输出结构稳定，失败时返回可诊断错误。
- 文档与实现保持一致。

## 2. 里程碑规划

| 里程碑 | 目标 | 主要产物 | 完成标准 |
|---|---|---|---|
| `M0` | 项目骨架与工程规范 | 双 Skill 目录、基础文档、配置样例 | 目录和命名稳定，能被后续任务引用。 |
| `M1` | `active-gerrit` 基础连通 | Basic Auth client、`doctor`、`whoami` | 能连通 Gerrit 3.11.2 并解析 XSSI JSON。 |
| `M2` | 只读 Code Review 能力 | query/get/list/diff/comments | Agent 能读取待评审 change 和 diff。 |
| `M3` | 安全写操作 | review/comment/vote/add-reviewer/wip/ready | 能发布评论、投票、添加 reviewer，具备 dry-run。 |
| `M4` | 高风险动作与缓存 | submit/rebase/abandon/cache/schema | 高风险动作有前置检查和显式确认。 |
| `M5` | `active-gerrit-workflow` MVP | review queue、review brief、pre-submit check | 业务流程层能复用基础层输出报告。 |
| `M6` | 验证、发布与维护 | 测试、README、安装说明、发布清单 | 可交付、可复现、可回归。 |
| `M7` | 本地 Git 封装能力 | git runner、repo status、fetch/checkout、push review | Agent 能安全完成本地 Git + Gerrit patch set 操作。 |
| `M8` | Git + Gerrit 工作流编排 | prepare-local-review、fix-and-upload、pre-push check | Workflow 层能编排 REST 与本地 Git。 |
| `M9` | `install.sh` 安装器 | 源码安装、配置引导、Skill 部署、update、installer doctor | 新用户能一键安装、升级和诊断本项目。 |

建议执行顺序：`M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6`，随后 `M7 -> M8` 与 `M9` 可并行推进；`M9` 的 P0 任务只依赖已有 `doctor` 和 Skill 目录。

## 3. 工作流总览

```text
用户请求
  |
  |-- Gerrit 基础操作 -> active-gerrit
  |     |-- doctor / whoami
  |     |-- query changes / get diff / review / submit
  |
  |-- 业务流程目标 -> active-gerrit-workflow
        |-- 调用 active-gerrit 获取原子数据
        |-- 应用业务规则
        |-- 输出 WorkflowReport
        |-- 必要时回退 active-gerrit 执行底层操作
```

## 4. M0：项目骨架与工程规范

### M0-T01 创建双 Skill 目录结构

- 优先级：`P0`
- 依赖：无
- 产物：
  - `active-gerrit/SKILL.md`
  - `active-gerrit/agents/openai.yaml`
  - `active-gerrit/references/`
  - `active-gerrit/scripts/`
  - `active-gerrit-workflow/SKILL.md`
  - `active-gerrit-workflow/agents/openai.yaml`
  - `active-gerrit-workflow/references/`
  - `active-gerrit-workflow/scripts/`
- TODO：
  - [x] 创建 `active-gerrit/` 目录。
  - [x] 创建 `active-gerrit-workflow/` 目录。
  - [x] 保持 Skill 目录内不放 README、安装指南等冗余文档。
  - [x] 在仓库根 README 引导用户看 `doc/` 和 Skill 目录。
- 验收：
  - [x] 两个 Skill 都有合法 `SKILL.md` frontmatter。
  - [x] 两个 `SKILL.md` 均不超过 500 行。
  - [x] 目录结构与方案文档一致。

### M0-T02 建立工程基础文件

- 优先级：`P0`
- 依赖：`M0-T01`
- 产物：
  - `.gitignore`
  - `requirements.txt` 或明确无第三方依赖说明
  - 示例环境变量文件，如 `.env.example`
- TODO：
  - [x] `.gitignore` 加入 `.cache/`、`.env`、`*.pyc`、`__pycache__/`。
  - [x] 创建 `.env.example`，包含 `GERRIT_BASE_URL`、`GERRIT_USERNAME`、`GERRIT_HTTP_PASSWORD`。
  - [x] 明确第一阶段只依赖 Python 标准库。
  - [x] 预留后续 `requirements.txt`。
- 验收：
  - [x] 敏感配置不会被 Git 默认跟踪。
  - [x] 新用户可以根据 `.env.example` 准备环境变量。

### M0-T03 梳理 reference 文档

- 优先级：`P0`
- 依赖：`M0-T01`
- 产物：
  - `active-gerrit/references/gerrit-rest-api-3.11.2.md`
  - `active-gerrit/references/core-workflows.md`
  - `active-gerrit/references/result-schemas.md`
  - `active-gerrit-workflow/references/business-workflows.md`
  - `active-gerrit-workflow/references/review-policies.md`
- TODO：
  - [x] 从 `doc/Gerrit REST API.md` 精简迁移基础 API 引用。
  - [x] 将通用 Gerrit 工作流放入 `core-workflows.md`。
  - [x] 将业务流程模板放入 `business-workflows.md`。
  - [x] 将标准输出结构放入 `result-schemas.md`。
- 验收：
  - [x] `SKILL.md` 能清晰说明何时读取哪个 reference。
  - [x] reference 不互相深层跳转，保持一层可发现。

## 5. M1：`active-gerrit` 基础连通

### M1-T01 实现低层 Gerrit HTTP Client

- 优先级：`P0`
- 依赖：`M0-T02`
- 产物：`active-gerrit/scripts/gerrit_client.py`
- TODO：
  - [x] 实现 Base URL 归一化。
  - [x] 实现 `/a/` 鉴权路径拼接。
  - [x] 实现 Basic Auth header 生成。
  - [x] 实现 `GET/POST/PUT/DELETE`。
  - [x] 实现重复 query 参数，如多个 `o=`。
  - [x] 实现 `Accept: application/json`。
  - [x] 实现超时和 TLS 验证配置。
  - [x] 实现 XSSI 前缀清理。
  - [x] 实现 JSON 和纯文本响应分流。
- 验收：
  - [x] 能请求 `/config/server/version`。
  - [x] 能请求 `/accounts/self/detail`。
  - [x] 密码和 Authorization header 不出现在日志和错误输出中。

### M1-T02 实现鉴权抽象

- 优先级：`P0`
- 依赖：`M1-T01`
- 产物：`AuthProvider` 设计
- TODO：
  - [x] 实现 `BasicAuthProvider`。
  - [x] 预留 `BearerTokenProvider`。
  - [x] 预留 `AccessTokenProvider`。
  - [x] 预留 `CookieXsrfProvider`。
  - [x] 预留 `AnonymousProvider`。
- 验收：
  - [x] 默认 `GERRIT_AUTH_TYPE=basic`。
  - [x] 未实现的鉴权类型返回清晰错误或预留提示。

### M1-T03 实现 CLI 基础入口

- 优先级：`P0`
- 依赖：`M1-T01`
- 产物：`active-gerrit/scripts/gerrit_cli.py`
- TODO：
  - [x] 实现 `argparse` 命令入口。
  - [x] 实现统一 JSON envelope。
  - [x] 实现统一错误 envelope。
  - [x] 实现 `--trace`、`--deadline`、`--no-cache` 预留参数。
  - [x] 实现 exit code 规范。
- 验收：
  - [x] 命令成功时输出 `{"ok": true, ...}`。
  - [x] 命令失败时输出 `{"ok": false, "error": ...}`。
  - [x] stderr 不泄露敏感信息。

### M1-T04 实现 `doctor`

- 优先级：`P0`
- 依赖：`M1-T03`
- 产物：`python scripts/gerrit_cli.py doctor`
- TODO：
  - [x] 检查 `python3 >= 3.9`。
  - [x] 检查 `curl`。
  - [x] 检查 `git`。
  - [x] 检查 `sed`。
  - [x] 检查 `GERRIT_BASE_URL`。
  - [x] 检查 Basic Auth 环境变量。
  - [x] 检查 Gerrit version。
  - [x] 检查 `accounts/self/detail`。
  - [x] 检查 XSSI 清理。
  - [x] 检查缓存目录可写。
  - [x] 检查可选命令 `jq`、`openssl`、`ssh`、`rg`。
- 验收：
  - [x] 成功环境下 `doctor.ok=true`。
  - [x] 缺少 `curl` 时给出明确安装提示。
  - [x] 鉴权失败时能区分 `401`、`403`。

### M1-T05 实现基础命令 `version` 与 `whoami`

- 优先级：`P0`
- 依赖：`M1-T03`
- 产物：
  - `python scripts/gerrit_cli.py version`
  - `python scripts/gerrit_cli.py whoami`
- TODO：
  - [x] `version` 调用 `GET /config/server/version`。
  - [x] `whoami` 调用 `GET /accounts/self/detail`。
  - [x] 输出标准化账号字段。
- 验收：
  - [x] 能返回 Gerrit `3.11.2`。
  - [x] 能返回 `_account_id`、username、email。

## 6. M2：只读 Code Review 能力

### M2-T01 实现 Change 查询

- 优先级：`P0`
- 依赖：`M1-T03`
- 命令：
  - `query-changes`
  - `query-preset`
- TODO：
  - [x] 支持 `--query`。
  - [x] 支持 `--option` 多次传入。
  - [x] 支持 `--limit`、`--start`。
  - [x] 支持 preset：`my_open_reviews`、`my_owned_open`、`project_open`。
  - [x] 输出 `ChangeSummary[]`。
- 验收：
  - [x] 能查询待我评审 changes。
  - [x] 能按项目和分支过滤。
  - [x] 不默认拉取过重字段。

### M2-T02 实现 Change 详情

- 优先级：`P0`
- 依赖：`M2-T01`
- 命令：`get-change`
- TODO：
  - [x] 支持 `--detail summary|detail|files|full`。
  - [x] 默认使用 `CURRENT_REVISION`、`DETAILED_ACCOUNTS`、`DETAILED_LABELS`、`SUBMIT_REQUIREMENTS`。
  - [x] 标准化输出 `ChangeDetail`。
  - [x] 支持 `--include-raw`。
- 验收：
  - [x] 能返回当前 patch set number。
  - [x] 能返回 labels 和 submit requirements。

### M2-T03 实现文件列表与 diff

- 优先级：`P0`
- 依赖：`M2-T02`
- 命令：
  - `list-files`
  - `get-diff`
  - `get-content`
- TODO：
  - [x] 支持 `--revision current`。
  - [x] 将 `current` 解析成具体 revision 用于缓存 key。
  - [x] 对 file path 统一 URL encode。
  - [x] 支持 `--context`。
  - [x] 支持 `--intraline`。
  - [x] 支持 `--ignore-whitespace`。
  - [x] 输出 `FileDiff`。
- 验收：
  - [x] 能读取指定文件 diff。
  - [x] 文件路径包含 `/` 时请求正常。
  - [x] diff 输出不丢失 Gerrit 原始关键字段。

### M2-T04 实现评论与消息读取

- 优先级：`P0`
- 依赖：`M2-T02`
- 命令：
  - `list-comments`
  - `list-drafts`
  - `list-messages`
  - `list-reviewers`
- TODO：
  - [x] 获取 published comments。
  - [x] 获取当前用户 drafts。
  - [x] 获取 change messages。
  - [x] 获取 reviewers 和 CC。
  - [x] 标准化账号字段。
- 验收：
  - [x] 能按文件路径组织 comments。
  - [x] 能识别 unresolved comments。

## 7. M3：评审写操作

### M3-T01 实现 ReviewInput 构造与校验

- 优先级：`P0`
- 依赖：`M2-T03`
- 产物：review payload builder
- TODO：
  - [x] 校验 label/value。
  - [x] 校验 comments 文件路径。
  - [x] 校验 line/range。
  - [x] 支持 patchset-level comment `/PATCHSET_LEVEL`。
  - [x] 默认 tag：`autogenerated:active-gerrit`。
  - [x] 默认 notify：`OWNER_REVIEWERS`。
- 验收：
  - [x] payload 符合 Gerrit `ReviewInput`。
  - [x] dry-run 能输出 `ReviewPlan`。

### M3-T02 实现 review/comment/vote 命令

- 优先级：`P0`
- 依赖：`M3-T01`
- 命令：
  - `review`
  - `comment`
  - `vote`
- TODO：
  - [x] 支持 `--input review.json`。
  - [x] 支持 `--message`。
  - [x] 支持 `--label Code-Review=1`。
  - [x] 支持 `--dry-run`。
  - [x] 支持 `--notify`。
  - [x] 执行前确认目标 revision。
- 验收：
  - [x] 能发布 patchset-level comment。
  - [x] 能发布 inline comment。
  - [x] 能投 `Code-Review` 或 `Verified`。

### M3-T03 实现 reviewer 管理

- 优先级：`P1`
- 依赖：`M2-T04`
- 命令：
  - `add-reviewer`
  - `remove-reviewer`
  - `delete-vote`
- TODO：
  - [x] 支持 reviewer account id、username、email。
  - [x] 支持 `--state REVIEWER|CC`。
  - [x] 支持 group reviewer 的 `confirmed`。
  - [x] 删除 reviewer 前展示目标账号。
  - [x] 删除 vote 前展示 label 和账号。
- 验收：
  - [x] 能添加 reviewer。
  - [x] 能添加 CC。
  - [x] 删除类操作默认 dry-run 或需要显式确认。

### M3-T04 实现轻量 Change 状态操作

- 优先级：`P1`
- 依赖：`M2-T02`
- 命令：
  - `set-wip`
  - `set-ready`
  - `set-topic`
  - `set-hashtags`
  - `attention-add`
  - `attention-remove`
- TODO：
  - [x] 所有命令支持 `--message` 或 `--reason`。
  - [x] 所有命令支持 `--notify`。
  - [x] 输出操作前后 change 状态摘要。
- 验收：
  - [x] 能设置 WIP/Ready。
  - [x] 能修改 topic 和 hashtags。

## 8. M4：高风险动作与缓存

### M4-T01 实现 submit 前检查

- 优先级：`P0`
- 依赖：`M2-T02`
- 命令：`submit --dry-run`
- TODO：
  - [x] 刷新 change detail。
  - [x] 获取 submit requirements。
  - [x] 获取 mergeable。
  - [x] 获取 submitted together。
  - [x] 检查 status 是否为 `NEW`。
  - [x] 检查 submit action 是否可用。
  - [x] 输出提交计划和阻塞原因。
- 验收：
  - [x] 不满足条件时不会 submit。
  - [x] 报告清楚列出缺失 label 或 requirement。

### M4-T02 实现 submit/rebase/abandon/restore

- 优先级：`P1`
- 依赖：`M4-T01`
- 命令：
  - `submit`
  - `rebase`
  - `abandon`
  - `restore`
- TODO：
  - [x] 默认 dry-run。
  - [x] 需要 `--yes` 才执行。
  - [x] 高风险操作执行前刷新状态。
  - [x] 输出 updated refs。
  - [x] 输出后续建议。
- 验收：
  - [x] `submit --dry-run` 不产生写操作。
  - [x] `submit --yes` 只在检查通过后执行。
  - [x] `abandon` 必须提供 message。

### M4-T03 实现缓存层

- 优先级：`P1`
- 依赖：`M2-T03`
- 产物：`active-gerrit/scripts/gerrit_cache.py`
- TODO：
  - [x] 实现缓存 key 生成。
  - [x] 实现 TTL。
  - [x] 实现 `--no-cache`。
  - [x] 实现 `--refresh`。
  - [x] 对 version、whoami 做缓存。
  - [ ] 对 projects、branches 做缓存（当前 CLI 尚无对应命令入口）。
  - [x] 对 query/get-change/list-files 做短 TTL 读缓存。
  - [x] 对具体 revision diff 做长缓存。
  - [x] 写操作前绕过关键状态缓存。
- 验收：
  - [x] 缓存不包含凭据。
  - [x] `current` revision 会先解析再缓存。
  - [x] 新 patch set 后不会误用旧 `current` 缓存。

### M4-T04 实现错误映射

- 优先级：`P1`
- 依赖：`M1-T03`
- 产物：`active-gerrit/scripts/gerrit_errors.py`
- TODO：
  - [x] 映射 `400`。
  - [x] 映射 `401`。
  - [x] 映射 `403`。
  - [x] 映射 `404`。
  - [x] 映射 `409`。
  - [x] 映射网络错误、TLS 错误、超时。
  - [x] 给出面向 Agent 的 hint。
- 验收：
  - [x] `404` 提示“资源不存在或当前用户不可见”。
  - [x] `403` 提示权限不足。
  - [x] `409` 提示状态冲突。

## 9. M5：`active-gerrit-workflow` MVP

### M5-T01 实现 workflow CLI 基础入口

- 优先级：`P1`
- 依赖：`M2-T02`
- 产物：`active-gerrit-workflow/scripts/workflow_cli.py`
- TODO：
  - [x] 实现 `doctor`。
  - [x] 支持 `ACTIVE_GERRIT_HOME`。
  - [x] 通过 subprocess 调用 `active-gerrit/scripts/gerrit_cli.py`。
  - [x] 统一输出 `WorkflowReport`。
  - [x] 记录 `used_active_gerrit_commands`。
- 验收：
  - [x] workflow doctor 能调用 active-gerrit doctor。
  - [x] 基础层不可用时给出明确错误。

### M5-T02 实现待评审队列流程

- 优先级：`P1`
- 依赖：`M5-T01`
- 命令：`my-review-queue`
- TODO：
  - [x] 调用 `query-preset my_open_reviews`。
  - [x] 按更新时间排序。
  - [x] 标记 WIP、private、unresolved comment。
  - [x] 标记缺少我的响应的 change。
  - [x] 输出队列报告。
- 验收：
  - [x] 能生成待评审清单。
  - [x] 每个 change 有建议下一步。

### M5-T03 实现单 Change 评审摘要

- 优先级：`P1`
- 依赖：`M5-T01`
- 命令：`review-brief`
- TODO：
  - [x] 调用 `get-change`。
  - [x] 调用 `list-files`。
  - [x] 对重点文件调用 `get-diff`。
  - [x] 汇总改动规模、风险文件、评论状态。
  - [x] 输出 `WorkflowReport`。
- 验收：
  - [x] 能对一个 change 生成评审摘要。
  - [x] 不自动发布评论。

### M5-T04 实现 submit 前业务检查

- 优先级：`P1`
- 依赖：`M4-T01`
- 命令：`pre-submit-check`
- TODO：
  - [x] 复用 `active-gerrit submit --dry-run`。
  - [x] 应用业务规则，如目标分支、owner、reviewer、label。
  - [x] 输出 blocked/warning/pass。
  - [x] 输出需要人工判断的事项。
- 验收：
  - [x] 不执行 submit。
  - [x] 报告包含业务阻塞原因。

### M5-T05 建立业务规则 reference

- 优先级：`P1`
- 依赖：`M5-T01`
- 产物：
  - `business-workflows.md`
  - `review-policies.md`
  - `release-policies.md`
  - `escalation-rules.md`
- TODO：
  - [x] 写明默认评审 checklist。
  - [x] 写明 release 分支策略占位。
  - [x] 写明 owner/reviewer 分派规则占位。
  - [x] 写明升级/阻塞规则。
- 验收：
  - [x] workflow SKILL 能准确引用这些文档。
  - [x] 业务规则缺失时流程输出 `needs_human_decision`。

## 10. M6：验证、发布与维护

### M6-T01 单元测试

- 优先级：`P1`
- 依赖：`M1-T03`
- TODO：
  - [ ] 测试 XSSI 清理。
  - [ ] 测试 URL encode。
  - [ ] 测试重复 query 参数。
  - [ ] 测试 Basic Auth header redaction。
  - [ ] 测试 error envelope。
  - [ ] 测试 cache key。
- 验收：
  - [ ] 单元测试可在无 Gerrit 环境下运行。

### M6-T02 集成测试

- 优先级：`P1`
- 依赖：`M2-T04`
- TODO：
  - [ ] 准备测试 Gerrit 账号。
  - [ ] 准备测试 project。
  - [ ] 测试 `doctor`。
  - [ ] 测试 `query-changes`。
  - [ ] 测试 `get-diff`。
  - [ ] 测试 dry-run review。
  - [ ] 在允许环境测试真实 review/comment。
- 验收：
  - [ ] 测试结果可复现。
  - [ ] 写操作测试不会影响生产项目。

### M6-T03 安全检查

- 优先级：`P0`
- 依赖：所有写操作
- TODO：
  - [ ] 检查日志不包含密码。
  - [ ] 检查 JSON 输出不包含 Authorization。
  - [ ] 检查 cache 不包含敏感凭据。
  - [ ] 检查 high-risk 命令需要 `--yes`。
  - [ ] 检查 submit 前必须刷新状态。
- 验收：
  - [ ] 人工 review 安全检查通过。

### M6-T04 文档和发布清单

- 优先级：`P1`
- 依赖：MVP 功能稳定
- TODO：
  - [ ] 更新根 README 的当前状态。
  - [ ] 更新 `doc/Gerrit Skill 封装方案.md` 中已完成项。
  - [ ] 增加部署前置检查说明。
  - [ ] 增加示例命令。
  - [ ] 增加发布 checklist。
- 验收：
  - [ ] 新用户能按文档完成 `doctor`。
  - [ ] 新用户能查询一个 change 并读取 diff。

## 11. 任务依赖图

```text
M0-T01
  -> M0-T02
  -> M0-T03
  -> M1-T01
      -> M1-T02
      -> M1-T03
          -> M1-T04
          -> M1-T05
          -> M2-T01
              -> M2-T02
                  -> M2-T03
                  -> M2-T04
                  -> M3-T01
                      -> M3-T02
                      -> M3-T03
                      -> M3-T04
                  -> M4-T01
                      -> M4-T02
                  -> M4-T03
                  -> M4-T04
                  -> M5-T01
                      -> M5-T02
                      -> M5-T03
                      -> M5-T04
                      -> M5-T05
          -> M6-T01
              -> M6-T02
              -> M6-T03
              -> M6-T04
M7-T00
  -> M7-T01
      -> M7-T02
          -> M7-T03
              -> M7-T04
              -> M7-T05
                  -> M7-T06
                      -> M8-T01
                  -> M7-T07
                      -> M7-T08
                          -> M8-T02
                          -> M8-T03
                          -> M8-T04
              -> M7-T09
              -> M7-T10
M9-T00
  -> M9-T01
      -> M9-T02
          -> M9-T03
              -> M9-T04
              -> M9-T05
                  -> M9-T06
                      -> M9-T07
                      -> M9-T08
                      -> M9-T09
                          -> M9-T10
                              -> M9-T11
                              -> M9-T12
```

## 12. MVP 范围

### 12.1 MVP 必须包含

- [ ] `active-gerrit/SKILL.md`
- [ ] `active-gerrit/scripts/gerrit_client.py`
- [ ] `active-gerrit/scripts/gerrit_cli.py`
- [ ] Basic Auth
- [ ] XSSI 清理
- [ ] `doctor`
- [ ] `whoami`
- [ ] `query-changes`
- [ ] `get-change`
- [ ] `list-files`
- [ ] `get-diff`
- [ ] `list-comments`
- [ ] `review --dry-run`
- [ ] `review --input`
- [x] `add-reviewer`
- [x] `submit --dry-run`

### 12.2 MVP 暂不包含

- [ ] 管理员接口自动执行。
- [ ] 插件启停。
- [ ] cache flush。
- [ ] index reindex。
- [ ] 批量删除 branch/tag。
- [ ] 复杂业务发布流程。
- [ ] 非 Basic Auth 的真实实现。

## 13. 质量门禁

### 13.1 合并前检查

- [ ] `python scripts/gerrit_cli.py doctor` 通过。
- [ ] 单元测试通过。
- [ ] dry-run 写操作不会产生实际 Gerrit 更新。
- [ ] 真实写操作必须需要 `--yes` 或明确输入文件。
- [ ] 输出 JSON schema 无破坏性变化。
- [ ] 文档已更新。

### 13.2 发布前检查

- [ ] 在测试 Gerrit 上完成 smoke test。
- [ ] 使用错误密码验证 `401` 错误提示。
- [ ] 使用无权限账号验证 `403` 错误提示。
- [ ] 使用不存在 change 验证 `404` 错误提示。
- [ ] 使用不可提交 change 验证 `409` 或 blocked 报告。
- [ ] 检查 `.cache/gerrit` 不含敏感信息。

## 14. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Gerrit 权限差异导致 API 失败 | Agent 无法完成任务 | 错误 envelope 中提供权限和可见性提示。 |
| 写操作误执行 | 影响真实 review 流程 | 高风险操作默认 dry-run，必须 `--yes`。 |
| Change 状态变化快 | 使用旧状态做决策 | 写操作前强制刷新 detail/mergeable/SR。 |
| 缓存污染 | 结果误导 Agent | 对 change 状态短 TTL，支持 `--refresh`。 |
| 凭据泄露 | 安全事故 | 日志和输出 redaction，缓存不存凭据。 |
| workflow 层重复实现 REST | 维护成本上升 | 强制 workflow 调用 active-gerrit。 |
| Skill 文档过大 | 占用上下文 | 使用 references 渐进加载。 |

## 15. GitHub Issues 拆分建议

可以按以下 issue 创建：

- [ ] `M0: scaffold active-gerrit and active-gerrit-workflow skill directories`
- [ ] `M1: implement Gerrit Basic Auth HTTP client`
- [ ] `M1: implement doctor/version/whoami`
- [ ] `M2: implement query-changes and query presets`
- [ ] `M2: implement get-change/list-files/get-diff`
- [ ] `M2: implement comments/messages/reviewers read APIs`
- [ ] `M3: implement review/comment/vote commands`
- [ ] `M3: implement reviewer management commands`
- [ ] `M4: implement submit dry-run and high-risk action guards`
- [ ] `M4: implement cache and result schema stabilization`
- [ ] `M5: implement workflow CLI and my-review-queue`
- [ ] `M5: implement review-brief and pre-submit-check`
- [ ] `M6: add unit/integration/safety tests`
- [ ] `M6: update docs and release checklist`

## 16. 当前下一步建议

建议立即开始的 5 个任务：

1. [ ] `M0-T01` 创建双 Skill 目录结构。
2. [ ] `M0-T02` 增加 `.gitignore` 和 `.env.example`。
3. [ ] `M1-T01` 实现 `gerrit_client.py` 的最小 GET 能力。
4. [ ] `M1-T03` 实现 `gerrit_cli.py` 基础 envelope。
5. [ ] `M1-T04` 实现 `doctor`，把 `curl`、`python3`、`git`、环境变量和 Gerrit 连通性检查固化。

## 17. M7：本地 Git 封装能力

> 本节追加于 `2026-05-11`，对应 [Gerrit Skill 封装方案.md](./Gerrit%20Skill%20封装方案.md) 第 17 节。
>
> 目标：在 `active-gerrit` 中补齐本地 Git 能力，让 Agent 能安全完成 repo 识别、工作区检查、patch set fetch/checkout、Change-Id 检查和 review push。

### M7-T00 本地 Git 命令调研与方案追加

- 优先级：`P0`
- 依赖：无
- 产物：
  - `doc/Gerrit Skill 封装方案.md` 第 17 节
  - `doc/Gerrit Skill 专项TODO.md` 第 17、18 节
- TODO：
  - [x] 检查本机 `git --version`。
  - [x] 检查当前仓库状态和 remote。
  - [x] 调研 `status/fetch/push/diff/commit/branch/remote/worktree/cherry-pick` 的机器可解析参数。
  - [x] 明确本地 Git 封装与 Gerrit REST 封装边界。
  - [x] 追加任务拆分和验收标准。
- 验收：
  - [x] 文档说明本地 Git 为什么需要封装。
  - [x] 文档包含命令清单、风险分级、schema 和工作流。

### M7-T01 设计并创建 Git CLI 模块骨架

- 优先级：`P0`
- 依赖：`M7-T00`
- 产物：
  - `active-gerrit/scripts/git_cli.py`
  - `active-gerrit/scripts/git_runner.py`
  - `active-gerrit/scripts/git_schemas.py`
  - `active-gerrit/scripts/git_gerrit.py`
- TODO：
  - [x] 创建独立 `git_cli.py`，避免继续膨胀 `gerrit_cli.py`。
  - [x] 复用或对齐 `gerrit_cli.py` 的 JSON envelope。
  - [x] 定义 `source: "git"`。
  - [x] 定义统一 exit code。
  - [x] 支持全局参数 `--repo`、`--timeout`、`--trace`、`--dry-run`、`--yes`。
  - [x] 在 `active-gerrit/SKILL.md` 增加本地 Git 触发和安全规则。
- 验收：
  - [x] `python scripts/git_cli.py --help` 可用。
  - [x] `python scripts/git_cli.py ping` 返回 `{"ok": true}`。
  - [x] 所有命令输出单个 JSON object。

### M7-T02 实现 GitRunner 安全执行层

- 优先级：`P0`
- 依赖：`M7-T01`
- 产物：`active-gerrit/scripts/git_runner.py`
- TODO：
  - [x] 使用参数数组调用 `subprocess.run`。
  - [x] 禁止 `shell=True`。
  - [x] 支持 `GIT_BIN`。
  - [x] 支持超时和 stdout/stderr 上限。
  - [x] 自动脱敏 remote URL、用户名密码、token。
  - [x] 实现 repo root 解析。
  - [x] 区分不在 Git 仓库、git 不存在、命令失败、超时。
- 验收：
  - [x] 不在 Git 仓库时返回 `GitConfigError`。
  - [x] 命令超时时返回 `GitCommandError` 或专用超时错误。
  - [x] 输出中不包含 remote URL 明文凭据。

### M7-T03 实现基础诊断和仓库状态命令

- 优先级：`P0`
- 依赖：`M7-T02`
- 命令：
  - `git-doctor`
  - `repo-info`
  - `repo-status`
  - `repo-remotes`
  - `repo-config`
- TODO：
  - [x] `git-doctor` 检查 `git --version`。
  - [x] `git-doctor` 检查 `user.name`、`user.email`。
  - [x] `git-doctor` 检查当前 repo、remote、upstream。
  - [x] `git-doctor` 检查可选 `commit-msg` hook。
  - [x] `repo-info` 输出 repo root、git dir、HEAD、branch、upstream、ahead/behind。
  - [x] `repo-status` 解析 `git status --porcelain=v1 --branch -z`。
  - [x] `repo-remotes` 输出脱敏 URL。
- 验收：
  - [x] 干净工作区返回 `is_clean=true`。
  - [x] 有 staged/unstaged/untracked 文件时能稳定分类。
  - [x] 没有 upstream 时输出 warning 而不是崩溃。

### M7-T04 实现本地 diff/log/branch 读取能力

- 优先级：`P1`
- 依赖：`M7-T03`
- 命令：
  - `repo-diff`
  - `repo-diff-file`
  - `repo-log`
  - `repo-show`
  - `repo-branches`
- TODO：
  - [x] `repo-diff` 支持 `--staged`、`--base`、`--stat-only`、`--include-patch`。
  - [x] 使用 `git diff --name-status -z` 解析文件状态。
  - [x] 使用 `git diff --numstat -z` 解析增删行。
  - [x] 单文件 diff 必须通过 `-- <path>` 传参。
  - [x] `repo-log` 使用 `--format` 输出结构化 commit。
  - [x] `repo-branches` 使用 `branch --format` 输出本地/远端分支。
- 验收：
  - [x] rename/copy/delete 文件能正确识别。
  - [x] 文件名包含空格时解析正确。
  - [x] 默认不输出大 patch，避免上下文过载。

### M7-T05 实现 Gerrit ref 和 remote 解析

- 优先级：`P0`
- 依赖：`M7-T03`
- 产物：`active-gerrit/scripts/git_gerrit.py`
- TODO：
  - [x] 从 `active-gerrit get-change` 结果读取 `RevisionInfo.ref`。
  - [x] 支持 fallback 构造 `refs/changes/<last-two>/<number>/<patch-set>`。
  - [x] 自动选择 Gerrit remote：优先 `--remote`，其次 `GERRIT_GIT_REMOTE`，再匹配 `GERRIT_BASE_URL`，最后 `origin`。
  - [x] 校验 remote URL 和 project 是否可能匹配。
  - [x] 标准化 Gerrit ref options，如 topic/reviewer/cc/hashtag/wip/ready。
- 验收：
  - [x] change number `4247` + patch set `3` 能生成 `refs/changes/47/4247/3`。
  - [x] REST 有 ref 时优先使用 REST ref。
  - [x] remote 无法判断时返回可诊断 warning。

### M7-T06 实现 patch set fetch/checkout/worktree

- 优先级：`P0`
- 依赖：`M7-T05`
- 命令：
  - `fetch-change`
  - `checkout-change`
  - `worktree-change`
- TODO：
  - [x] `fetch-change` 调用 REST 获取 change detail 和 revision ref。
  - [x] `fetch-change` 执行 `git fetch <remote> <ref>`。
  - [x] fetch 后解析 `FETCH_HEAD` 或返回 fetched commit。
  - [x] `checkout-change` 默认要求工作区干净。
  - [x] `checkout-change` 支持创建 `review/<change>-<patchset>` 分支。
  - [x] `worktree-change` 支持创建独立目录，避免污染当前工作区。
  - [x] dirty worktree 时给出 worktree 建议。
- 验收：
  - [x] 能拉取指定 Gerrit patch set。
  - [x] dirty worktree 下默认拒绝 checkout。
  - [x] worktree 模式不会修改当前工作区。

### M7-T07 实现 Change-Id 与提交辅助

- 优先级：`P1`
- 依赖：`M7-T04`
- 命令：
  - `change-id-check`
  - `commit-plan`
  - `commit-create`
  - `commit-amend`
- TODO：
  - [x] 从 HEAD 或 message 文件提取 `Change-Id`。
  - [x] 检查 `commit-msg` hook 是否存在。
  - [x] `commit-plan` 输出 staged/unstaged 文件、message 摘要、Change-Id 状态。
  - [x] `commit-create` 默认只提交显式 paths。
  - [x] `commit-amend` 默认要求保留旧 `Change-Id`。
  - [x] commit message 通过临时文件传递。
- 验收：
  - [x] 缺少 `Change-Id` 时返回 warning 或 validation error。
  - [x] `commit-amend` 改变 `Change-Id` 时默认拒绝。
  - [x] 不会意外提交未指定文件。

### M7-T08 实现 review push 计划和 dry-run

- 优先级：`P0`
- 依赖：`M7-T05`、`M7-T07`
- 命令：
  - `push-review-plan`
  - `push-review`
- TODO：
  - [x] 构造 `HEAD:refs/for/<branch>` refspec。
  - [x] 支持 `--topic`、`--reviewer`、`--cc`、`--hashtag`、`--wip`、`--ready`。
  - [x] 默认执行计划或 `git push --dry-run --porcelain`。
  - [x] 执行前要求工作区干净。
  - [x] 展示 remote、branch、HEAD、subject、Change-Id、target ref。
  - [x] `--yes` 才允许真实 push。
  - [x] 禁止 `--force`，后续只考虑 `--force-with-lease`。
- 验收：
  - [x] dry-run 不产生远端更新。
  - [x] refspec 编码稳定可测试。
  - [x] push 被拒绝时能返回 Gerrit/Git 诊断。

### M7-T09 补充 Git 结果 schema 和 reference

- 优先级：`P1`
- 依赖：`M7-T03`
- 产物：
  - `active-gerrit/references/git-workflows.md`
  - `active-gerrit/references/result-schemas.md` Git schema 扩展
- TODO：
  - [ ] 增加 `GitRepoInfo`。
  - [ ] 增加 `GitStatus`。
  - [ ] 增加 `GitDiffSummary`。
  - [ ] 增加 `GitChangeCheckout`。
  - [ ] 增加 `GitPushReviewPlan`。
  - [ ] 在 `SKILL.md` 说明何时读取 `git-workflows.md`。
- 验收：
  - [ ] schema 与 `git_cli.py` 输出一致。
  - [ ] Agent 能按 reference 完成本地拉取、修复、上传流程。

### M7-T10 测试本地 Git 封装

- 优先级：`P1`
- 依赖：`M7-T02` 至 `M7-T09`
- 产物：
  - `tests/test_git_runner.py`
  - `tests/test_git_cli.py`
  - `tests/test_git_gerrit.py`
- TODO：
  - [ ] 使用临时目录 `git init` 构造本地仓库。
  - [ ] 使用 bare repo 模拟 remote。
  - [ ] 测试 `status --porcelain -z` parser。
  - [ ] 测试文件名包含空格、rename、delete。
  - [ ] 测试 remote URL 脱敏。
  - [ ] 测试 Gerrit ref 构造。
  - [ ] 测试 push-review dry-run refspec。
  - [ ] 测试 dirty worktree 保护。
- 验收：
  - [ ] 单元测试不依赖真实 Gerrit。
  - [ ] 真实 push 只在显式集成测试环境执行。

## 18. M8：Git + Gerrit 工作流编排

> 目标：在 `active-gerrit-workflow` 中编排 REST 与本地 Git，形成贴近日常研发的高层流程。

### M8-T01 实现本地评审准备流程

- 优先级：`P1`
- 依赖：`M7-T06`
- 命令：`prepare-local-review`
- TODO：
  - [ ] 调用 `active-gerrit get-change --detail full`。
  - [ ] 调用 `git_cli.py repo-info` 校验 repo。
  - [ ] 调用 `git_cli.py repo-status` 检查工作区。
  - [ ] dirty 时建议 `worktree-change`。
  - [ ] 调用 `fetch-change` 和 `checkout-change` 或 `worktree-change`。
  - [ ] 输出下一步测试和评审建议。
- 验收：
  - [ ] 用户给一个 change 即可得到本地评审环境。
  - [ ] 不会覆盖用户未提交改动。

### M8-T02 实现本地修复并上传 patch set 流程

- 优先级：`P1`
- 依赖：`M7-T07`、`M7-T08`
- 命令：`fix-and-upload-patchset`
- TODO：
  - [ ] 检查当前 repo 是否对应目标 change。
  - [ ] 汇总本地 diff。
  - [ ] 生成 `commit-amend` 计划。
  - [ ] 校验 Change-Id 保持不变。
  - [ ] 生成 `push-review-plan`。
  - [ ] 默认不执行真实 commit/push，除非用户明确确认。
  - [ ] push 后用 REST 刷新 change detail。
- 验收：
  - [ ] 能上传新 patch set。
  - [ ] Change-Id 不被误改。
  - [ ] 输出包含新 patch set 或刷新后的 change 摘要。

### M8-T03 实现从本地分支创建 review 流程

- 优先级：`P2`
- 依赖：`M7-T08`
- 命令：`create-review-from-branch`
- TODO：
  - [ ] 检查当前分支、upstream、ahead/behind。
  - [ ] 检查 HEAD commit subject 和 Change-Id。
  - [ ] 生成 push review 计划。
  - [ ] 支持 topic/reviewer/cc/hashtag。
  - [ ] push 后通过 `query-changes` 解析新 change。
- 验收：
  - [ ] 能从本地 HEAD 创建 Gerrit review。
  - [ ] 如果 Change-Id 缺失，流程不会静默创建不可追踪提交。

### M8-T04 实现 pre-push 安全检查流程

- 优先级：`P1`
- 依赖：`M7-T03`、`M7-T07`、`M7-T08`
- 命令：`pre-push-review-check`
- TODO：
  - [ ] 检查工作区是否干净。
  - [ ] 检查 HEAD 是否领先目标 upstream。
  - [ ] 检查 commit message、Change-Id、作者信息。
  - [ ] 检查目标 branch 和 remote。
  - [ ] 输出 blocked/warning/pass。
- 验收：
  - [ ] 可以作为 `push-review` 前置报告。
  - [ ] blocked 时不执行 push。

### M8-T05 更新 GitHub Issues 拆分建议

- 优先级：`P2`
- 依赖：`M7` 方案稳定
- TODO：
  - [ ] `M7: implement local git runner and repo status commands`
  - [ ] `M7: implement Gerrit change fetch and checkout helpers`
  - [ ] `M7: implement Change-Id and commit helpers`
  - [ ] `M7: implement push-review plan and dry-run`
  - [ ] `M8: implement local review preparation workflow`
  - [ ] `M8: implement fix and upload patch set workflow`
- 验收：
  - [ ] 每个 issue 都能独立验收。
  - [ ] 高风险 push/commit 工作有明确安全说明。

## 19. M9：`install.sh` 安装器

> 本节追加于 `2026-05-11`，对应 [install.sh 实现方案.md](./install.sh%20实现方案.md)。
>
> 目标：让新用户可以通过 `install.sh` 完成源码下载、环境检查、Gerrit 运行配置、Skill 部署和后续升级；让已有用户可以通过 `install.sh doctor/update` 维护本地安装。

### M9-T00 安装器方案调研与任务规划

- 优先级：`P0`
- 依赖：无
- 产物：
  - `doc/install.sh 实现方案.md`
  - `doc/Gerrit Skill 专项TODO.md` 第 19 节
- TODO：
  - [x] 调研 Oh My Zsh、nvm、Homebrew、asdf 的源码安装和更新模式。
  - [x] 明确 XDG 目录、配置文件、缓存和状态文件布局。
  - [x] 明确交互式配置和 `NONINTERACTIVE=1` 自动化配置。
  - [x] 明确 Skill 软链接和目录复制两种部署模式。
  - [x] 明确 `update`、安全边界和测试策略。
- 验收：
  - [x] 方案文档包含命令设计、目录布局、安全策略、测试方案和实施阶段。
  - [x] TODO 文档包含可执行、可验收的安装器任务拆分。

### M9-T01 建立 `install.sh` CLI 骨架

- 优先级：`P0`
- 依赖：`M9-T00`
- 产物：`install.sh`
- 命令：
  - `install.sh install`
  - `install.sh doctor`
  - `install.sh config`
  - `install.sh deploy-skill`
  - `install.sh update`
  - `install.sh help`
- TODO：
  - [x] 使用 Bash 实现，并设置 `set -Eeuo pipefail`。
  - [x] 实现 `main`、`parse_args`、`dispatch_command`。
  - [x] 支持默认子命令 `install`。
  - [x] 支持全局参数 `--repo-url`、`--ref`、`--install-dir`、`--config-file`、`--skill-dir`、`--skill-mode`。
  - [x] 支持 `--non-interactive`、`--yes`、`--force`、`--verbose`。
  - [x] 实现统一日志函数 `info/warn/error/die`。
  - [x] 实现 `--help` 帮助文本。
- 验收：
  - [x] `bash install.sh --help` 返回 0。
  - [x] `bash install.sh help` 返回 0。
  - [x] 未知参数返回非 0，并输出可诊断错误。
  - [x] 输出中没有 Bash trace 或未脱敏内部变量。

### M9-T02 实现安装路径、状态文件和配置文件基础设施

- 优先级：`P0`
- 依赖：`M9-T01`
- 产物：
  - XDG 路径解析函数
  - `$CONFIG_DIR/env`
  - `$CONFIG_DIR/install-state`
- TODO：
  - [x] 实现 `XDG_DATA_HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME`、`XDG_STATE_HOME` 默认路径。
  - [x] 支持 `ACTIVE_GERRIT_WORKFLOW_HOME` 覆盖源码安装目录。
  - [x] 支持 `ACTIVE_GERRIT_WORKFLOW_ENV_FILE` 覆盖配置文件。
  - [x] 支持 `ACTIVE_GERRIT_SKILL_DIR` 覆盖 Skill 目标目录。
  - [x] 创建配置目录时尽量设置权限 `0700`。
  - [x] 写入配置文件时使用临时文件 + 原子 `mv`。
  - [x] 配置文件权限设置为 `0600`。
  - [x] 实现安装状态文件读写，记录 install dir、skill dir、skill mode、repo、ref、commit。
- 验收：
  - [x] 默认路径落在 `${XDG_DATA_HOME:-$HOME/.local/share}` 和 `${XDG_CONFIG_HOME:-$HOME/.config}`。
  - [x] 配置文件权限为 `0600`。
  - [x] 重复执行不会破坏已有状态文件。
  - [x] 路径中包含空格时仍能正常工作。

### M9-T03 实现源码分发和安装目录管理

- 优先级：`P0`
- 依赖：`M9-T02`
- 产物：源码 clone/update 函数
- TODO：
  - [x] 定义默认 `DEFAULT_REPO_URL` 和 `DEFAULT_REF`。
  - [x] 支持通过 `ACTIVE_GERRIT_WORKFLOW_REPO` 和 `ACTIVE_GERRIT_WORKFLOW_REF` 覆盖。
  - [x] 源码目录不存在时执行 `git clone --origin origin --branch <ref>`。
  - [x] 源码目录已存在且是本仓库时进入校验或更新路径。
  - [x] 源码目录存在但不是 Git repo 时默认失败。
  - [x] `--force` 时先备份冲突目录，再重新 clone。
  - [x] 检查 remote URL 与期望 repo 是否一致，不一致时提示。
  - [x] 支持从远程 `curl | bash` 方式运行时安装完整源码。
- 验收：
  - [x] 干净机器上能把仓库克隆到默认安装目录。
  - [x] 已安装时重复运行不会重复 clone。
  - [x] 冲突目录不会被静默覆盖。
  - [x] `install-state` 能记录当前 commit。

### M9-T04 实现依赖检查和安装器 `doctor`

- 优先级：`P0`
- 依赖：`M9-T01`、`M9-T02`
- 命令：`install.sh doctor`
- TODO：
  - [x] 检查 `bash`。
  - [x] 检查 `git`。
  - [x] 检查 `python3 >= 3.9`。
  - [x] 检查 `curl` 或 `wget`。
  - [x] 检查 `sed`。
  - [x] 检查可选依赖 `jq`、`openssl`、`ssh`、`rg`、`shellcheck`、`bats`。
  - [x] 检查安装目录、配置目录、缓存目录、状态目录是否可读写。
  - [x] 加载配置文件后调用 `active-gerrit/scripts/gerrit_cli.py doctor`。
  - [x] 设置 `ACTIVE_GERRIT_HOME` 后调用 `active-gerrit-workflow/scripts/workflow_cli.py doctor`。
  - [x] 支持 `--json` 输出机器可读结果。
- 验收：
  - [x] 缺少必需依赖时返回非 0，并给出安装建议。
  - [x] 缺少可选依赖时只输出 warning。
  - [x] Python doctor 的错误被脱敏并可诊断。
  - [x] `install.sh doctor` 不需要真实写入 Gerrit。

### M9-T05 实现 Gerrit 运行配置引导

- 优先级：`P0`
- 依赖：`M9-T02`、`M9-T04`
- 命令：`install.sh config`
- TODO：
  - [x] 交互输入 `GERRIT_BASE_URL`。
  - [x] 交互输入 `GERRIT_USERNAME`。
  - [x] 使用 `read -s` 静默输入 `GERRIT_HTTP_PASSWORD`。
  - [x] 支持选择是否保存 HTTP Password。
  - [x] 默认写入 `GERRIT_AUTH_TYPE=basic`。
  - [x] 写入 `GERRIT_VERIFY_SSL`、`GERRIT_TIMEOUT_SECONDS`、`GERRIT_DEFAULT_NOTIFY`、`GERRIT_CACHE_DIR`。
  - [x] 已有配置存在时读取旧值作为默认值。
  - [x] 覆盖配置前创建 `.bak.<timestamp>` 备份。
  - [x] `NONINTERACTIVE=1` 下从环境变量读取必填项，缺项直接失败。
- 验收：
  - [x] 交互模式能生成可 source 的 env 文件。
  - [x] 非交互模式不读取 stdin。
  - [x] 输出中只显示 `GERRIT_HTTP_PASSWORD=<redacted>`。
  - [x] 错误密码场景由 `doctor` 返回清晰认证错误。

### M9-T06 实现 Skill 部署

- 优先级：`P0`
- 依赖：`M9-T02`、`M9-T03`
- 命令：`install.sh deploy-skill`
- TODO：
  - [ ] 校验源码目录中存在 `active-gerrit/SKILL.md`。
  - [ ] 校验源码目录中存在 `active-gerrit-workflow/SKILL.md`。
  - [ ] 默认 Skill 目标目录为 `${CODEX_HOME:-$HOME/.codex}/skills`。
  - [ ] 实现 `symlink` 模式部署两个完整 Skill 目录。
  - [ ] 正确软链接已存在时跳过。
  - [ ] 错误软链接默认提示，`--force` 时重建。
  - [ ] 用户自有目录默认不覆盖。
  - [ ] `copy` 模式复制完整 Skill 目录，排除 `__pycache__`、`*.pyc`、`.cache`、`.git`。
  - [ ] copy 模式目标存在时先备份或使用安全同步策略。
- 验收：
  - [ ] symlink 模式下目标目录有两个正确软链接。
  - [ ] copy 模式下目标目录有两个完整 Skill 副本。
  - [ ] 目标冲突不会被静默覆盖。
  - [ ] 部署后 `workflow_cli.py doctor` 能解析 sibling `active-gerrit` 或 `ACTIVE_GERRIT_HOME`。

### M9-T07 实现一键更新

- 优先级：`P0`
- 依赖：`M9-T03`、`M9-T04`、`M9-T06`
- 命令：`install.sh update`
- TODO：
  - [ ] 读取 `install-state` 定位源码目录、repo、ref、skill mode。
  - [ ] 检查源码目录是否为 Git repo。
  - [ ] 检查 working tree 是否干净。
  - [ ] 默认脏工作区时停止，不执行 `reset --hard` 或 `git clean`。
  - [ ] 执行 `git fetch --tags --prune`。
  - [ ] branch 安装时执行 `git pull --ff-only`。
  - [ ] tag/commit 安装时支持 `git checkout --detach <ref>`。
  - [ ] 更新前记录 previous HEAD。
  - [ ] 更新后重新执行 `deploy-skill`。
  - [ ] 更新后运行 `install.sh doctor`。
  - [ ] 更新失败时输出手动恢复命令。
- 验收：
  - [ ] 没有更新时仍能完成依赖和 Skill 校验。
  - [ ] 有 fast-forward 更新时能更新源码和 Skill。
  - [ ] 脏工作区默认不会被修改。
  - [ ] 更新失败不会自动破坏用户改动。

### M9-T08 生成 launcher 和可选 shell profile 集成

- 优先级：`P1`
- 依赖：`M9-T02`、`M9-T05`
- 产物：
  - `$BIN_DIR/active-gerrit`
  - `$BIN_DIR/active-gerrit-workflow`
  - `$BIN_DIR/active-gerrit-install`
- TODO：
  - [ ] 支持 `ACTIVE_GERRIT_WORKFLOW_BIN_DIR` 覆盖 bin 目录。
  - [ ] 生成 `active-gerrit` launcher，自动 source 配置并执行 `gerrit_cli.py`。
  - [ ] 生成 `active-gerrit-workflow` launcher，自动 source 配置并执行 `workflow_cli.py`。
  - [ ] 生成 `active-gerrit-install` launcher 指向安装器。
  - [ ] 检查 `$BIN_DIR` 是否在 `PATH` 中。
  - [ ] 用户确认后向 shell profile 写入受控 source block。
  - [ ] 支持 `PROFILE=/dev/null` 和 `--no-profile`。
  - [ ] 已存在 source block 时更新而不是重复追加。
- 验收：
  - [ ] launcher 可执行且能传递参数。
  - [ ] profile 中不直接写入密码。
  - [ ] `--no-profile` 不修改任何 shell profile。
  - [ ] 重复安装不会重复追加 profile block。

### M9-T09 安全与用户体验加固

- 优先级：`P1`
- 依赖：`M9-T01` 至 `M9-T08`
- TODO：
  - [ ] 实现统一 `redact`，覆盖密码、token、cookie、Authorization header。
  - [ ] 远端 repo URL 被覆盖时打印安全 warning。
  - [ ] 交互安装前展示安装计划并要求确认。
  - [ ] 自动安装系统依赖必须显式 `--install-deps`。
  - [ ] 不默认使用 `sudo`。
  - [ ] 所有覆盖操作都先备份或要求 `--force`。
  - [ ] 所有临时文件写入失败时清理残留。
  - [ ] trap 捕获失败并输出 next step。
  - [ ] 新增 `install.sh status` 输出安装摘要。
  - [ ] 预留 `install.sh uninstall`，默认只展示删除计划。
- 验收：
  - [ ] stdout/stderr 不包含真实 `GERRIT_HTTP_PASSWORD`。
  - [ ] 用户自有 Skill 目录不会被误删。
  - [ ] `status` 能展示 install dir、config file、skill dir、skill mode、commit。
  - [ ] `uninstall` 不默认删除配置和缓存。

### M9-T10 安装器测试

- 优先级：`P1`
- 依赖：`M9-T01` 至 `M9-T09`
- 产物：
  - `tests/install/`
  - ShellCheck 检查
  - Bats 测试或等价 shell 测试
- TODO：
  - [ ] `shellcheck install.sh` 通过。
  - [ ] 测试 `help` 和未知参数。
  - [ ] 测试非交互缺必填 Gerrit 配置时失败。
  - [ ] 测试配置文件权限为 `0600`。
  - [ ] 测试密码脱敏。
  - [ ] 测试 symlink 部署。
  - [ ] 测试 copy 部署。
  - [ ] 测试 Skill 目标冲突保护。
  - [ ] 测试 update 脏工作区保护。
  - [ ] 使用 fake `python3` 或 fixture 模拟两个 Python doctor。
- 验收：
  - [ ] 安装器测试不依赖真实 Gerrit。
  - [ ] 安装器测试可在临时目录中重复运行。
  - [ ] CI 或本地一条命令能跑完 shell 测试。

### M9-T11 更新文档与发布说明

- 优先级：`P1`
- 依赖：`M9-T01` 至 `M9-T10`
- TODO：
  - [ ] 更新 README，增加一键安装命令。
  - [ ] 更新 README，增加 `install.sh doctor/update` 用法。
  - [ ] 更新 README，说明配置文件位置和凭据安全。
  - [ ] 更新 `doc/install.sh 实现方案.md` 中已完成项和真实 repo URL。
  - [ ] 在发布 checklist 中加入安装器 smoke test。
  - [ ] 增加离线/内网环境安装说明。
- 验收：
  - [ ] 新用户能仅按 README 完成安装。
  - [ ] 新用户能定位配置文件并运行 doctor。
  - [ ] 文档中的命令与实际 `install.sh --help` 一致。

### M9-T12 GitHub Issues 拆分建议

- 优先级：`P2`
- 依赖：`M9` 方案稳定
- TODO：
  - [ ] `M9: implement install.sh CLI skeleton and XDG path handling`
  - [ ] `M9: implement source clone and installer doctor`
  - [ ] `M9: implement interactive Gerrit config`
  - [ ] `M9: implement Skill deployment by symlink/copy`
  - [ ] `M9: implement update command`
  - [ ] `M9: add launcher/profile integration`
  - [ ] `M9: add installer shell tests and docs`
- 验收：
  - [ ] 每个 issue 都能独立验收。
  - [ ] 安全敏感 issue 明确列出脱敏、权限和覆盖保护要求。

## 20. 更新后的下一步建议

鉴于 `M0` 到 `M5` 多数核心能力已经完成，建议下一步改为：

1. [ ] `M9-T01` 到 `M9-T06` 先完成安装器 P0 闭环：CLI、路径、源码安装、配置、doctor、Skill 部署。
2. [ ] `M9-T07` 实现 `update`，让安装器能支撑后续分发和升级。
3. [ ] `M6-T01` 到 `M6-T03` 补齐现有 REST/workflow 测试与安全检查。
4. [x] `M7-T06` 完成本地 Gerrit patch set fetch/checkout，补齐 REST + Git 混合闭环。
5. [ ] `M9-T10` 增加安装器 shell 测试，确保一键安装不会随功能迭代回归。
