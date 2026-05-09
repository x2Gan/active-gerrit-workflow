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

建议执行顺序：`M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6`。

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
  - [ ] 复用 `active-gerrit submit --dry-run`。
  - [ ] 应用业务规则，如目标分支、owner、reviewer、label。
  - [ ] 输出 blocked/warning/pass。
  - [ ] 输出需要人工判断的事项。
- 验收：
  - [ ] 不执行 submit。
  - [ ] 报告包含业务阻塞原因。

### M5-T05 建立业务规则 reference

- 优先级：`P1`
- 依赖：`M5-T01`
- 产物：
  - `business-workflows.md`
  - `review-policies.md`
  - `release-policies.md`
  - `escalation-rules.md`
- TODO：
  - [ ] 写明默认评审 checklist。
  - [ ] 写明 release 分支策略占位。
  - [ ] 写明 owner/reviewer 分派规则占位。
  - [ ] 写明升级/阻塞规则。
- 验收：
  - [ ] workflow SKILL 能准确引用这些文档。
  - [ ] 业务规则缺失时流程输出 `needs_human_decision`。

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
