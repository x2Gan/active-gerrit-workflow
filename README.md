# Active Gerrit Workflow

面向 Agent 的 Gerrit Code Review REST API Skill 封装项目。

本仓库用于沉淀 Gerrit REST API 的版本化调研、Skill 设计、工具封装与常见评审工作流，让 Agent 能够稳定地查询变更、读取 diff、发表评论、投票、管理 reviewer，并辅助完成 Gerrit Code Review 日常操作。

## 项目目标

Gerrit 的 REST API 覆盖面很广，但直接给 Agent 使用时会遇到一些固定问题：

- API 路径多，`/changes/`、`/projects/`、`/accounts/` 等资源模型需要统一抽象。
- JSON 响应带 XSSI 前缀 `)]}'`，普通 JSON parser 不能直接解析。
- `change-id`、`revision-id`、`project-name`、`file-id` 的 URL 编码和解析规则容易出错。
- Change 查询语法和 `o=` 返回字段选项需要按场景裁剪，否则结果过少或请求过重。
- Review、comment、vote、submit、rebase 等动作都有权限和状态约束，需要把错误信息转成 Agent 能理解的反馈。

本项目希望把这些细节封装成可复用的 Gerrit Skill，让 Agent 不只是“知道 Gerrit API”，而是能按可靠流程完成代码评审协作。

## 当前状态

项目处于 Skill 骨架与资料整理阶段。

已完成：

- Gerrit Code Review `3.11.2` REST API 调研文档。
- 面向 Agent/Skill 的接口分层建议。
- 常用 Gerrit 工作流和 payload 模板整理。
- 双 Skill 基础目录：`active-gerrit/` 与 `active-gerrit-workflow/`。
- Skill reference 文档：REST 精简索引、通用工作流、结果 schema、业务流程和评审策略。

正在规划：

- REST client 基础封装。
- Gerrit 查询、diff、review、submit 等核心工具。
- 示例配置与本地验证脚本。

## 适配版本

当前调研和设计以本地部署的 Gerrit Code Review `3.11.2` 为基准。

详细 API 文档见：

- [doc/Gerrit REST API.md](doc/Gerrit%20REST%20API.md)

官方版本文档入口：

- <https://gerrit-documentation.storage.googleapis.com/Documentation/3.11.2/rest-api.html>

## 预期能力

第一阶段计划封装 Agent 最常用的 Gerrit 操作：

| 能力 | 说明 |
|---|---|
| 连接验证 | 获取 Gerrit 版本、当前账号、账号权限。 |
| 变更查询 | 按 owner、reviewer、project、branch、status、label 等条件查询 changes。 |
| 变更详情 | 获取 change detail、labels、submit requirements、messages、current revision。 |
| 文件与 diff | 列出 patch set 文件，读取文件内容，获取指定文件 diff。 |
| 评论与投票 | 发布 review message、inline comments、patchset-level comments、Code-Review/Verified 投票。 |
| Reviewer 管理 | 添加 reviewer、添加 CC、查询 reviewer votes、删除 reviewer。 |
| Change 动作 | submit、abandon、restore、rebase、set WIP、set ready。 |
| 项目查询 | 列出 projects、branches、tags，读取 project config。 |

第二阶段会扩展：

- Change Edit 文件修改与发布。
- Project access、labels、submit requirements 管理。
- Group/account 管理。
- 管理员接口：cache、index、tasks、plugins。
- Git + REST 混合工作流。

## Skill 设计方向

建议将 Skill 拆成基础能力层与业务流程层。完整目标结构：

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

核心设计原则：

- `active-gerrit` 是 Gerrit REST API 基础能力层，也是其他 Gerrit 工作流的 fallback。
- `active-gerrit-workflow` 是业务流程层，复用 `active-gerrit` 的脚本和标准输出。
- 两个 `SKILL.md` 只保留 Agent 必须遵循的流程、工具选择规则和安全约束。
- 详细 REST API 与业务规则放入对应 `references/`，按需读取，避免上下文过载。
- 可重复、易出错的 HTTP 请求逻辑放入 `active-gerrit/scripts/`，业务编排放入 `active-gerrit-workflow/scripts/`。

## 推荐配置

后续 Skill/工具默认读取环境变量。可以从 `.env.example` 开始准备本地配置：

```bash
cp .env.example .env
```

最小必填项：

```bash
export GERRIT_BASE_URL="https://gerrit.example.com"
export GERRIT_AUTH_TYPE="basic"
export GERRIT_USERNAME="alice"
export GERRIT_HTTP_PASSWORD="********"
```

说明：

- `GERRIT_BASE_URL` 是 Gerrit Web 根地址。
- `GERRIT_AUTH_TYPE` 第一阶段默认是 `basic`。
- `GERRIT_USERNAME` 是 Gerrit 用户名。
- `GERRIT_HTTP_PASSWORD` 是 Gerrit UI 中生成的 HTTP password，不一定是登录密码。
- 需要认证的 REST 请求会使用 `/a/` 前缀和 HTTP Basic Auth。
- `.env` 已被 `.gitignore` 忽略，避免本地凭据被默认提交。

## 依赖说明

第一阶段只依赖 Python 标准库。`requirements.txt` 作为后续第三方依赖的占位文件，目前不需要安装任何包。

最小连通性验证：

```bash
curl -sS \
  -u "$GERRIT_USERNAME:$GERRIT_HTTP_PASSWORD" \
  -H "Accept: application/json" \
  "$GERRIT_BASE_URL/a/accounts/self/detail" |
sed "1{/^)]}'/d;}"
```

## Agent 使用示例

封装完成后，期望 Agent 可以处理类似任务：

```text
帮我查看 myProject 中所有待我评审的 open changes，并按更新时间排序。
```

```text
读取 change 4247 的当前 patch set，汇总改动文件和主要风险点。
```

```text
查看 src/main/App.java 的 diff，在第 42 行留一条 unresolved inline comment。
```

```text
如果 change 4247 已满足 submit requirements，帮我提交它。
```

```text
把 alice@example.com 加为 reviewer，把 bob@example.com 加为 CC。
```

## REST 封装注意事项

实现工具时需要特别处理：

- 去除 Gerrit JSON 响应的 XSSI 前缀。
- 对 project、branch、file path、change id 做 URL encode。
- 默认使用推荐 change 标识：`<project>~<changeNumber>`。
- 默认 revision 使用 `current`。
- 查询 change 时按任务选择 `o=` 字段，避免一次性拉取过多数据。
- 对 `404` 提示“资源不存在或当前用户不可见”。
- 对 `403` 提示权限不足和可能需要的 Gerrit capability。
- 对 `409` 提示当前 Gerrit 状态冲突，例如不可 submit、merge conflict、change 已关闭。

## 目录说明

当前仓库结构：

```text
.
├── active-gerrit/
│   ├── SKILL.md
│   ├── agents/
│   │   └── openai.yaml
│   ├── references/
│   │   ├── gerrit-rest-api-3.11.2.md
│   │   ├── core-workflows.md
│   │   └── result-schemas.md
│   └── scripts/
├── active-gerrit-workflow/
│   ├── SKILL.md
│   ├── agents/
│   │   └── openai.yaml
│   ├── references/
│   │   ├── business-workflows.md
│   │   └── review-policies.md
│   └── scripts/
├── README.md
└── doc/
    ├── Gerrit REST API.md
    ├── Gerrit Skill 封装方案.md
    └── Gerrit Skill 专项TODO.md
```

阅读建议：先看 `doc/Gerrit Skill 封装方案.md` 理解分层设计，再看 `doc/Gerrit Skill 专项TODO.md` 跟踪任务；实现细节落在两个 Skill 目录中。

## 路线图

- [x] 梳理 Gerrit `3.11.2` REST API 文档。
- [x] 输出面向 Agent 的 REST API 参考文档。
- [x] 创建双 Skill 目录结构与最小 `SKILL.md`。
- [x] 建立基础工程文件与环境变量样例。
- [x] 拆分 Skill reference 文档。
- [x] 实现低层 Gerrit REST client。
- [ ] 实现查询 change、获取 diff、发布 review 的核心工具。
- [ ] 增加 submit/rebase/abandon 等 change action 工具。
- [ ] 增加项目、分支、标签查询工具。
- [ ] 增加本地验证脚本和示例任务。

## 贡献方式

欢迎围绕以下方向补充：

- 新版本 Gerrit REST API 差异。
- 本地 Gerrit 部署中的认证、权限、代理兼容问题。
- 常见 Code Review 工作流。
- Agent 调用 Gerrit 时的失败案例和错误处理策略。
- Skill 工具设计与测试用例。

提交变更时请尽量说明：

- 适配的 Gerrit 版本。
- 涉及的 REST endpoint。
- 是否需要管理员权限。
- 是否会产生写操作或通知用户。

## License

当前仓库暂未声明开源许可证。正式发布前建议补充 `LICENSE` 文件。
