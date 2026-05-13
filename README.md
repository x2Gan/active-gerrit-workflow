# Active Gerrit Workflow

面向 Agent 的 Gerrit 能力仓库：把 Gerrit REST API、稳定 JSON schema、本地 Git 工作流和可运维的安装器整理成一套可复用的 Skill 交付物。

这个仓库适合两类人：

- 想让 GitHub Copilot、Codex 或内部 Agent 安全地查询 Gerrit、读取 diff、发表评论、投票、提交或准备 patch set 的团队。
- 想把 Gerrit 能力拆成“基础能力层 + 工作流层”，并通过 reference 文档渐进加载上下文的维护者。

## 仓库亮点

- 双层 Skill 结构：`active-gerrit` 负责 Gerrit REST API 与本地 Git 原子能力，`active-gerrit-workflow` 负责编排评审流程。
- 安全默认值：高风险写操作默认 dry-run 或显式确认；日志、JSON 输出、repo URL 中的敏感信息都会脱敏。
- Git + Gerrit 混合能力：除了 REST 查询与 review，还支持本地 `repo-status`、`fetch-change`、`checkout-change`、`worktree-change`、`push-review-plan` 等工作流。
- 可落地安装器：提供 `install.sh`、XDG 目录布局、运行配置、Skill 部署、launcher 生成、状态检查和更新入口。
- Reference 驱动：把 REST 规则、Git 工作流、结果 schema、业务流程与评审策略拆到 `references/`，减少 Agent 上下文噪音。

## 当前实现范围

| 模块 | 当前状态 | 代表能力 |
|---|---|---|
| `active-gerrit` | 已可用 | `doctor`、`version`、`whoami`、`query-changes`、`get-change`、`list-files`、`get-diff`、`get-content`、`list-comments`、`list-messages`、`review`、`comment`、`vote`、`add-reviewer`、`submit --dry-run`、缓存与错误映射。 |
| 本地 Git CLI | 已可用 | `git-doctor`、`repo-info`、`repo-status`、`repo-diff`、`repo-log`、`repo-show`、`repo-branches`、`fetch-change`、`checkout-change`、`worktree-change`、`change-id-check`、`commit-plan`、`commit-create`、`commit-amend`、`push-review-plan`、`push-review`。 |
| `active-gerrit-workflow` | 已有 MVP | `doctor`、`my-review-queue`、`review-brief`、`pre-submit-check`。 |
| `install.sh` | 已可用 | `install`、`config`、`deploy-skill`、`doctor`、`update`、`status`、`uninstall`，以及 launcher/profile 集成。 |

> 当前 `install.sh install` 的职责是把源码同步到本地安装目录；`config`、`deploy-skill`、`doctor` 仍然是显式后续步骤。这样可以更好地支持内网镜像、自定义 Skill 目录和 `--no-profile` 场景。

## 兼容性与依赖

- Gerrit 基线版本：`3.11.2`
- Shell/运行时：`bash`、`git`、`python3 >= 3.9`、`sed`、`curl` 或 `wget`
- 可选工具：`jq`、`openssl`、`ssh`、`rg`、`shellcheck`、`bats`
- Python 依赖：当前以标准库为主，`requirements.txt` 仅作为后续扩展预留

## 快速开始

### 1. 源码安装

推荐先用 `git clone` 拉取源码，再运行安装器。这个方式可以复用本机 GitHub 凭据，适合私有仓库和企业网络环境：

```bash
gh auth login
gh auth setup-git
git clone https://github.com/active-ailab/active-gerrit-workflow.git
cd active-gerrit-workflow
bash install.sh install
```

如果已经提前创建并进入了空目录，也可以直接克隆到当前目录：

```bash
git clone https://github.com/active-ailab/active-gerrit-workflow.git .
bash install.sh install
```

> 如果仓库是私有仓库，匿名访问 `raw.githubusercontent.com` 会返回 404。推荐使用 GitHub CLI 的 Contents API 拉取安装脚本，并用 `gh auth setup-git` 让后续源码 clone 复用本机凭据。

私有仓库安装入口：

```bash
gh auth login
gh auth setup-git
mkdir -p active-gerrit-workflow
cd active-gerrit-workflow
bash -c "$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)"
```

也可以指定源码安装目录：

```bash
bash -c "$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)" -- --install-dir /path/to/active-gerrit-workflow
```

如果本机没有安装 GitHub CLI，可以使用具备仓库 `Contents: Read` 权限的 token：

```bash
export GITHUB_TOKEN="github_pat_xxx"
mkdir -p active-gerrit-workflow
cd active-gerrit-workflow
curl -fsSL \
  -H "Authorization: Bearer ${GITHUB_TOKEN:?}" \
  -H 'Accept: application/vnd.github.raw+json' \
  'https://api.github.com/repos/active-ailab/active-gerrit-workflow/contents/install.sh?ref=main' | bash
```

公开仓库仍可直接使用 GitHub Raw：

```bash
mkdir -p active-gerrit-workflow
cd active-gerrit-workflow
bash -c "$(curl -fsSL https://raw.githubusercontent.com/active-ailab/active-gerrit-workflow/main/install.sh)"
```

默认源码安装目录是运行安装器时的当前工作目录。也可以用 `--install-dir` 或 `ACTIVE_GERRIT_WORKFLOW_HOME` 显式覆盖。

### 2. 写入 Gerrit 运行配置

```bash
./install.sh config
```

`config` 会交互引导填写 Gerrit 连接信息，并把结果写入 `~/.config/active-gerrit-workflow/env`。如果已有配置，安装器会把旧值作为默认值展示，直接回车即可保留。

交互过程中会询问：

- Gerrit base URL，例如 `https://gerrit.example.com`
- Gerrit username
- 是否把 Gerrit HTTP password 保存到 env 文件
- Gerrit HTTP password（静默输入，输出中会脱敏）
- 是否校验 TLS 证书：`true` 或 `false`
- HTTP timeout seconds
- 默认 Gerrit notify policy，例如 `OWNER_REVIEWERS`
- Gerrit cache directory

`GERRIT_HTTP_PASSWORD` 指的是 Gerrit Web 页面中生成的 HTTP 凭据，通常不是网页登录密码，也不是公司 SSO/LDAP 密码。请在 Gerrit 的 `Settings` -> `HTTP Credentials` / `HTTP Password` 页面生成或复制该密码，并确认 `GERRIT_USERNAME` 与该页面展示的用户名一致。配置后可以用 `active-gerrit doctor` 或 `active-gerrit whoami` 验证认证是否成功；如果 `/a/accounts/self/detail` 返回 `401 Unauthorized`，优先重新生成并填写 Gerrit HTTP password。

这些值也可以先用环境变量预填，例如 `GERRIT_BASE_URL`、`GERRIT_USERNAME`、`GERRIT_HTTP_PASSWORD`、`GERRIT_VERIFY_SSL`、`GERRIT_TIMEOUT_SECONDS`、`GERRIT_DEFAULT_NOTIFY`、`GERRIT_CACHE_DIR`。

如果不希望安装器修改 shell profile，可以显式禁用：

```bash
./install.sh config --no-profile
```

### 3. 部署 Skill 并生成 launchers

```bash
./install.sh deploy-skill
```

如果你希望把 Skill 复制到目标目录，而不是创建软链接：

```bash
./install.sh deploy-skill --skill-mode copy
```

### 4. 运行检查与日常维护

完成 `config` 或 `deploy-skill` 后，安装器会生成以下 launcher：

- `active-gerrit`
- `active-gerrit-workflow`
- `active-gerrit-install`

常用命令：

```bash
active-gerrit-install doctor
active-gerrit-install status
active-gerrit-install update
active-gerrit doctor
active-gerrit-workflow doctor
```

`active-gerrit doctor` 默认输出人类可读的健康检查摘要；如果需要给脚本或 CI 使用原始 JSON，运行：

```bash
active-gerrit doctor --json
```

如果当前 shell 还没有拿到 `~/.local/bin` 的 PATH 更新，先直接使用完整路径：

```bash
~/.local/bin/active-gerrit-install status
```

## 非交互与自动化安装

在 CI、脚本化部署或无人值守环境里，可以先完成源码同步，再用环境变量写入运行配置：

```bash
NONINTERACTIVE=1 \
GERRIT_BASE_URL=https://gerrit.example.com \
GERRIT_USERNAME=alice \
GERRIT_HTTP_PASSWORD=replace-with-gerrit-http-password \
./install.sh config --no-profile
```

然后部署 Skill：

```bash
./install.sh deploy-skill --skill-mode copy --no-profile
```

最后做一次机器可读诊断：

```bash
active-gerrit-install doctor --json
```

## 默认路径与凭据安全

| 项目 | 默认路径 | 说明 |
|---|---|---|
| 源码安装目录 | 运行安装器时的当前工作目录 | `install.sh install` 同步后的源码 checkout。 |
| 配置目录 | `~/.config/active-gerrit-workflow` | 安装器和运行配置的主目录。 |
| 运行配置文件 | `~/.config/active-gerrit-workflow/env` | 可被 shell `source` 的 Gerrit 运行时配置。 |
| 安装状态文件 | `~/.config/active-gerrit-workflow/install-state` | 记录 install dir、skill dir、skill mode、repo、ref、commit。 |
| 缓存目录 | `~/.cache/active-gerrit-workflow` | 安装器缓存与 Gerrit 缓存入口。 |
| 状态目录 | `~/.local/state/active-gerrit-workflow` | 安装器运行状态。 |
| launcher 目录 | `~/.local/bin` | `active-gerrit*` launchers 默认写到这里。 |
| Skill 目标目录 | `${CODEX_HOME:-$HOME/.codex}/skills` | `deploy-skill` 的默认目标。 |

凭据与安全约束：

- `env` 文件默认权限是 `0600`。
- shell profile 中只写受控 source block，不直接写密码。
- stdout/stderr 和 JSON 输出会统一脱敏 `password`、`token`、`cookie`、`Authorization` 和带凭据的 URL。
- 安装器不会默认执行 `sudo`。
- `update` 默认拒绝脏工作区，不会自动 `reset --hard` 或 `git clean`。
- `uninstall` 当前是 plan-only，不会默认删除文件。

最小配置样例见 [.env.example](.env.example)。

## 离线 / 内网安装

如果运行环境不能直接访问 GitHub Raw，推荐两种方式：

### 方式 A：使用内网 Git 镜像

```bash
git clone https://git.example.com/platform/active-gerrit-workflow.git
cd active-gerrit-workflow
bash install.sh install --repo-url https://git.example.com/platform/active-gerrit-workflow.git --ref main
./install.sh config
./install.sh deploy-skill
./install.sh doctor
```

### 方式 B：从本地 checkout 安装

```bash
git clone https://git.example.com/platform/active-gerrit-workflow.git
cd active-gerrit-workflow
bash install.sh install --repo-url "$PWD" --ref main
./install.sh config --no-profile
./install.sh deploy-skill --no-profile
./install.sh doctor
```

说明：

- `--repo-url` 可以是 GitHub URL、内网 mirror URL，也可以是本地仓库路径。
- 离线环境仍然建议预装 `bash`、`git`、`python3`、`sed`，以及 `curl` 或 `wget` 中至少一个。
- 如果你不希望安装器改写登录环境，统一使用 `--no-profile` 或 `PROFILE=/dev/null`。

## 典型使用场景

```text
帮我查看 reviewer:self 的 open changes，并按更新时间排序。
```

```text
把 change 4247 的 current patch set 拉到本地 review worktree，并给我一个 review plan。
```

```text
检查当前分支是否可以安全 push 到 Gerrit，并生成 refs/for 计划。
```

```text
读取一个 change 的主要风险文件、评论状态和 submit requirements，输出评审摘要。
```

## 文档导航

| 文档 | 适合什么时候读 |
|---|---|
| [doc/Gerrit Skill 封装方案.md](doc/Gerrit%20Skill%20封装方案.md) | 想理解整体分层设计、风险分级和能力边界时。 |
| [doc/Gerrit Skill 专项TODO.md](doc/Gerrit%20Skill%20专项TODO.md) | 想看任务拆分、里程碑和当前进度时。 |
| [doc/install.sh 实现方案.md](doc/install.sh%20实现方案.md) | 想看安装器设计、目录布局、测试方案与发布 checklist 时。 |
| [doc/Gerrit REST API.md](doc/Gerrit%20REST%20API.md) | 想快速回看 Gerrit 3.11.2 REST API 调研索引时。 |
| [active-gerrit/references/result-schemas.md](active-gerrit/references/result-schemas.md) | 想消费 Gerrit/Git CLI JSON 输出时。 |
| [active-gerrit/references/git-workflows.md](active-gerrit/references/git-workflows.md) | 想做本地 fetch、checkout、worktree、push-review 时。 |
| [active-gerrit-workflow/references/business-workflows.md](active-gerrit-workflow/references/business-workflows.md) | 想看工作流层的业务编排模板时。 |

## 仓库结构

```text
.
├── active-gerrit/
│   ├── SKILL.md
│   ├── references/
│   └── scripts/
├── active-gerrit-workflow/
│   ├── SKILL.md
│   ├── references/
│   └── scripts/
├── doc/
├── tests/
├── install.sh
└── README.md
```

其中：

- `active-gerrit/scripts/` 包含 Gerrit REST CLI、本地 Git CLI、缓存、错误映射和 Git/Gerrit 辅助模块。
- `active-gerrit-workflow/scripts/` 当前聚焦工作流编排入口。
- `tests/` 同时覆盖 Gerrit CLI、Git CLI、workflow CLI 和安装器回归。

## 开发与验证

常用验证命令：

```bash
python -m unittest tests.test_gerrit_cli tests.test_workflow_cli
python -m unittest tests.test_git_cli tests.test_git_gerrit
bash tests/install/run.sh
```

如果你在补安装器文档或发布流程，至少应重新跑一次：

```bash
bash tests/install/run.sh
```

## 当前仓库状态

- Gerrit REST CLI、Git CLI、workflow MVP 和安装器主流程都已落地。
- 首页和安装器文档已经按当前命令面更新，可直接作为 GitHub 首页入口与新用户上手指南。
- 当前仓库仍未声明开源许可证；正式对外发布前建议补充 `LICENSE`。


---

Gan GAN
Zepp Health, Active BU AI Lab
Copyright (c) 2026 Zepp Health. All rights reserved.
