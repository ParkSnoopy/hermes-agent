---
sidebar_position: 2
title: "斜杠命令参考"
description: "交互式 CLI 和消息平台斜杠命令完整参考"
---

# 斜杠命令参考

Hermes 有两个斜杠命令入口，均由 `hermes_cli/commands.py` 中的中央 `COMMAND_REGISTRY` 驱动：

- **交互式 CLI 斜杠命令** — 由 `cli.py` 分发，支持从注册表自动补全
- **消息平台斜杠命令** — 由 `gateway/run.py` 分发，帮助文本和平台菜单均从注册表生成

已安装的 skill（技能）也会在两个入口以动态斜杠命令的形式暴露。（`/plan` 曾是其中之一；它现在是内置命令 — 见下方 Session 表。）

## 交互式 CLI 斜杠命令

在 CLI 中输入 `/` 可打开自动补全菜单。内置命令不区分大小写。

### 会话

| 命令 | 描述 |
|---------|-------------|
| `/new [name]`（别名：`/reset`） | 开始新会话（全新会话 ID + 历史记录）。可选的 `[name]` 设置初始会话标题——例如 `/new my-experiment` 打开一个已命名为 `my-experiment` 的新会话，便于之后用 `/resume` 或 `/sessions` 查找。追加 `now`、`--yes` 或 `-y` 可跳过确认弹窗——例如 `/reset now`、`/new --yes my-experiment`。 |
| `/clear` | 清屏并开始新会话 |
| `/history` | 显示对话历史 |
| `/save <json\|md\|html> [文件名] [redact]` | 将当前会话导出为 JSON 快照、Markdown 记录或独立的 HTML 页面（也接受别名 `markdown` 和 `snapshot`）。`redact` 会在写入前清除 API 密钥、令牌和凭据，且始终放在可选文件名之后的最后一位：`/save md notes.md redact`。即使会话运行在远程网关上，文件也写在你自己的机器上（不支持 `session.export` 的旧版网关只能在其自身主机上保存默认命名的 JSON）。CLI 和 TUI：指定的文件名相对于当前目录解析，否则自动命名并保存在 `~/.hermes/sessions/saved/` 下。Desktop 会打开保存对话框（如提供文件名则预先填入）。直接输入 `/save` 在经典 CLI 中显示用法，在 TUI 和 Desktop 中保存为 JSON。 |
| `/retry` | 重试最后一条消息（重新发送给 agent） |
| `/undo` | 移除最后一轮用户/助手对话 |
| `/title` | 为当前会话设置标题（用法：/title My Session Name） |
| `/compress [focus topic]` | 手动压缩对话上下文（刷新记忆 + 摘要）。可选的焦点主题可缩小摘要保留的范围。 |
| `/rollback` | 列出或恢复文件系统检查点（用法：/rollback [number]） |
| `/snapshot [create\|restore <id>\|prune]`（别名：`/snap`） | 创建或恢复 Hermes 配置/状态的快照。`create [label]` 保存快照，`restore <id>` 回滚到该快照，`prune [N]` 删除旧快照，不带参数则列出所有快照。在经典 `hermes chat` 中，恢复会先关闭本地读取句柄、排空本地 Gateway，再离线恢复并重新连接：快照中仍有当前会话时载入恢复后的历史，否则创建新会话。多路复用中的非启动 profile 仅暂停并重新挂载自身，不重启其他 profile；恢复启动 profile 则会排空整个 Gateway。底层恢复仍保留独占维护门禁和 SQLite 安全检查，无法释放的占用仍会阻止恢复。 |
| `/stop` | 终止所有正在运行的后台进程 |
| `/queue <prompt>`（别名：`/q`） | 将 prompt（提示词）加入队列等待下一轮处理（不会中断当前 agent 响应）。 |
| `/steer <prompt>` | 在**下一次工具调用之后**向 agent 注入一条中途说明——不中断、不产生新的用户轮次。当前工具完成后，该文本会追加到最后一条工具结果的内容中，在不打断当前工具调用循环的情况下为 agent 提供新上下文。可用于在任务进行中调整方向（例如在 agent 运行测试时说"专注于 auth 模块"）。 |
| `/goal <text>` | 设置一个持续目标，Hermes 将跨轮次持续推进——这是我们对 Ralph loop 的实现。每轮结束后，辅助裁判模型会判断目标是否完成；若未完成，Hermes 自动继续。子命令：`/goal status`、`/goal pause`、`/goal resume`、`/goal clear`。预算默认为 20 轮（`goals.max_turns`）；任何真实用户消息都会抢占继续循环，状态在 `/resume` 后保留。完整说明见 [持续目标](../user-guide/features/goals.md)。 |
| `/subgoal <text>` | 在循环进行中向活动目标追加一个用户自定义条件。继续 prompt 会将所有子目标原文呈现给 agent，裁判也会将其纳入 DONE/CONTINUE 判断——因此只有原始目标**和**所有子目标都满足时，目标才会被标记为完成。子命令：`/subgoal`（列出）、`/subgoal remove <N>`、`/subgoal clear`。需要有活动的 `/goal`。 |
| `/resume [name]` | 恢复之前命名的会话 |
| `/sessions` | 在交互式选择器中浏览并恢复历史会话 |
| `/redraw` | 强制完整重绘 UI（在 tmux 调整大小、鼠标选择产生残影等导致终端错位后恢复）。 |
| `/status` | 显示会话信息——模型、提供商、profile、会话 ID、工作目录、标题、创建/更新时间戳、token 总量、agent 运行状态——随后显示本地**会话摘要**块（近期用户/助手轮次数、工具结果数、最常用工具、最近访问的文件、最新用户 prompt 和最新助手回复）。摘要从内存中的对话本地计算，不调用 LLM，不影响 prompt 缓存。 |
| `/agents`（别名：`/tasks`） | 显示当前会话中的活动 agent 和运行中的任务。 |
| `/bg <prompt>` | 在独立的后台会话中运行 prompt。agent 独立处理你的 prompt——当前会话保持空闲可继续其他工作。任务完成后结果以面板形式显示。见 [CLI 后台会话](../user-guide/cli.md#background-sessions)。 |
| `/btw <question>` | 在不打断当前工作的情况下，就**当前对话**提出一个快速的顺带问题。由一次一次性辅助 LLM 调用根据对话快照作答——实时会话的历史和提示缓存不受影响，当前回合继续运行。需要全新上下文的独立任务请使用 `/bg`。 |
| `/branch [name]`（别名：`/fork`） | 分支当前会话（探索不同路径） |


### 配置

| 命令 | 描述 |
|---------|-------------|
| `/config` | 显示当前配置 |
| `/model [model-name]` | 显示或更改当前模型。支持：`/model claude-sonnet-4`、`/model provider:model`（切换提供商）、`/model custom:model`（自定义端点）、`/model custom:name:model`（命名自定义提供商）、`/model custom`（从端点自动检测）、`--reasoning <level>`（在同一步、同一作用域下设置推理强度：`none`、`minimal` … `ultra`；选择器在选完模型后会多出一步推理强度选择，目录标记为无推理控制的路线会跳过该步），以及用户自定义别名（`/model fav`、`/model grok`——见[自定义模型别名](#custom-model-aliases)）。使用 `--global` 将更改持久化到 config.yaml。**注意：** `/model` 只能在已配置的提供商之间切换。如需添加新提供商，请退出会话后在终端运行 `hermes model`。 |
| `/codex-runtime [auto\|codex_app_server\|on\|off]` | 切换 OpenAI/Codex 模型的可选 [Codex app-server runtime](../user-guide/features/codex-app-server-runtime)。`auto`（默认）使用 Hermes 标准 chat completions；`codex_app_server` 将轮次交给 `codex app-server` 子进程，支持原生 shell、apply_patch、ChatGPT 订阅认证和迁移的 Codex 插件。下次会话生效。 |
| `/personality` | 设置预定义的 personality（人格） |
| `/verbose` | 循环切换工具进度显示：off → new → all → verbose。可通过配置[为消息平台启用](#notes)。 |
| `/fast [normal\|fast\|status]` | 切换快速模式——OpenAI Priority Processing / Anthropic Fast Mode。选项：`normal`、`fast`、`status`。 |
| `/reasoning` | 管理推理力度和显示（用法：/reasoning [level\|show\|hide]） |
| `/skin` | 显示或更改显示皮肤/主题 |
| `/statusbar`（别名：`/sb`） | 切换上下文/模型状态栏的显示与隐藏 |
| `/voice [on\|off\|tts\|status]` | 切换 CLI 语音模式和语音播放。录音使用 `voice.record_key`（默认：`Ctrl+B`）。 |
| `/yolo` | 切换 YOLO 模式——跳过所有危险命令审批提示。 |
| `/footer [on\|off\|status]` | 切换最终回复中的 gateway 运行时元数据页脚（显示模型、工具调用次数、耗时）。 |
| `/busy [queue\|steer\|interrupt\|status]` | 控制 Hermes 工作时发送消息的行为——将新消息加入队列、中途引导，或立即中断。支持 CLI 和消息 gateway。 |
| `/autoprune [on\|off\|later\|status]` | 决定是否自动清理旧的已结束 session（默认关闭）。当 `state.db` 所在磁盘空间不足时 Hermes 会询问一次；`on`/`off` 保存 `sessions.auto_prune` 并不再询问，`later` 一周后再提醒。支持 CLI、TUI 和 Desktop。 |
| `/indicator [kaomoji\|emoji\|unicode\|ascii]` | 仅限 CLI：选择 TUI 忙碌指示器样式。 |

### 工具与 Skill

| 命令 | 描述 |
|---------|-------------|
| `/tools [list\|disable\|enable] [name...]` | 管理工具：列出可用工具，或为当前会话禁用/启用特定工具。禁用工具会将其从 agent 工具集中移除并触发会话重置。 |
| `/toolsets` | 列出可用工具集 |
| `/browser [connect\|disconnect\|status]` | 管理本地 Chromium 系浏览器的 CDP 连接。`connect` 将浏览器工具附加到正在运行的 Chrome、Brave、Chromium 或 Edge 实例（默认：`http://127.0.0.1:9222`）。`disconnect` 断开连接。`status` 显示当前连接状态。若未检测到调试器，则自动启动支持的 Chromium 系浏览器。 |
| `/skills` | 从在线注册表搜索、安装、检查或管理 skill |
| `/memory [pending\|approve\|reject\|approval]` | 审核由写入审批门控（`memory.write_approval`）暂存的待处理 memory 写入，并切换该门控。见 [Memory 功能](../user-guide/features/memory.md)。 |
| `/bundles` | 列出已配置的 skill bundle——即一次预加载多个 skill 的 `/<name>` 斜杠别名。在 `~/.hermes/config.yaml` 的 `bundles:` 下配置。见 [Skills 功能](../user-guide/features/skills.md)。 |
| `/cron` | 管理定时任务（列出、添加/创建、编辑、暂停、恢复、运行、删除） |
| `/suggestions [accept\|dismiss N\|catalog\|clear]`（别名：`/suggest`） | 审核建议的自动化。使用 `/suggestions` 列出待处理建议，`/suggestions accept <id>` 接受并创建建议任务，`/suggestions dismiss <id>` 拒绝单条建议，`/suggestions catalog` 添加精选起步自动化，`/suggestions clear` 清理已解决的建议记录。被接受的任务会保留当前表面作为投递来源。 |
| `/blueprint [name] [slot=value ...]`（别名：`/bp`） | 通过 blueprint 模板设置自动化。裸 `/blueprint` 列出目录；`/blueprint <name>` 会在下一次 agent 轮次启动引导式填槽流程；`/blueprint <name> slot=value ...` 直接创建任务。 |
| `/curator` | 后台 skill 维护——`status`、`run`、`pin`、`archive`。见 [Curator](../user-guide/features/curator.md)。 |
| `/reload-mcp`（别名：`/reload_mcp`） | 从 config.yaml 重新加载 MCP 服务器 |
| `/reload-skills`（别名：`/reload_skills`） | 重新扫描 `~/.hermes/skills/` 以发现新安装或已删除的 skill |
| `/reload` | 将 `.env` 变量重新加载到运行中的会话（无需重启即可获取新 API 密钥） |
| `/plugins` | 列出已安装的插件及其状态 |

### 信息

| 命令 | 描述 |
|---------|-------------|
| `/help` | 显示帮助信息 |
| `/version` | 显示 Hermes Agent 版本、构建及环境信息。 |
| `/usage` | 显示 token 用量、费用明细、会话时长，以及——当活动提供商支持时——从提供商 API 实时拉取的**账户限额**部分，包含剩余配额/积分/套餐用量。 |
| `/credits` | 显示你的 Nous 积分余额和充值跳转链接。 |
| `/billing` | Nous 的 CLI 终端计费流程——查看余额、购买积分并管理自动充值 / 月度限额。 |
| `/insights` | 显示用量洞察和分析（最近 30 天） |
| `/paste` | 附加剪贴板图片 |
| `/copy [number]` | 将最后一条助手回复复制到剪贴板（或用数字指定倒数第 N 条）。仅限 CLI。 |
| `/image <path>` | 为下一条 prompt 附加本地图片文件。 |
| `/debug` | 上传调试报告（系统信息 + 日志）并获取可分享链接。消息平台中也可用。 |
| `/profile` | 显示活动 profile 名称和主目录 |

### 退出

| 命令 | 描述 |
|---------|-------------|
| `/quit` | 退出 CLI（也可用：`/exit`）。关于 `/q` 请参见上方 `/queue` 的说明。传入 `--delete`（或 `-d`）——例如 `/exit --delete`——可在退出前永久删除当前会话的 SQLite 历史记录和磁盘上的转录文件。适用于隐私敏感或一次性任务。 |

### 动态 CLI 斜杠命令

| 命令 | 描述 |
|---------|-------------|
| `/<skill-name>` | 将任意已安装的 skill 作为按需命令加载。示例：`/gif-search`、`/github-pr-workflow`、`/excalidraw`。 |
| `/skills ...` | 从注册表和官方可选 skill 目录搜索、浏览、检查、安装、审计、发布和配置 skill。 |

### 快捷命令

用户自定义快捷命令将一个短斜杠命令映射到 shell 命令或另一个斜杠命令。在 `~/.hermes/config.yaml` 中配置：

```yaml
quick_commands:
  status:
    type: exec
    command: systemctl status hermes-agent
  deploy:
    type: exec
    command: scripts/deploy.sh
  inbox:
    type: alias
    target: /gmail unread
```

然后在 CLI 或消息平台中输入 `/status`、`/deploy` 或 `/inbox`。快捷命令在分发时解析，可能不会出现在所有内置自动补全/帮助表中。

不支持将纯字符串 prompt 快捷方式作为快捷命令。较长的可复用 prompt 请放入 skill，或使用 `type: alias` 指向现有斜杠命令。

### 自定义模型别名

为常用模型定义自己的短名称，然后在 CLI 或任意消息平台中通过 `/model <alias>` 调用。别名在两者中的行为完全一致，支持仅会话（默认）和 `--global` 切换。

支持两种配置格式：

**完整格式** — 固定精确的模型、提供商，以及可选的 base URL。写入 `~/.hermes/config.yaml`：

```yaml
model_aliases:
  fav:
    model: claude-sonnet-4.6
    provider: anthropic
  grok:
    model: grok-4
    provider: x-ai
  ollama-qwen:
    model: qwen3-coder:30b
    provider: custom
    base_url: http://localhost:11434/v1
```

**简短格式** — 用一个字符串表示 `provider/model`。无需编辑 YAML，直接从 shell 设置：

```bash
hermes config set model.aliases.fav anthropic/claude-opus-4.6
hermes config set model.aliases.grok x-ai/grok-4
```

然后在聊天中：

```
/model fav            # 仅当前会话
/model grok --global  # 同时将当前模型更改持久化到 config.yaml
```

用户别名优先于内置短名称，因此将别名命名为 `sonnet`、`kimi`、`opus` 等会覆盖内置名称。别名名称不区分大小写。

### 别名解析

命令支持前缀匹配：输入 `/h` 解析为 `/help`，`/mod` 解析为 `/model`。当前缀有歧义（匹配多个命令）时，注册表顺序中的第一个匹配项优先。完整命令名和已注册别名始终优先于前缀匹配。

## 注意事项

- `/skin`、`/snapshot`、`/reload`、`/tools`、`/toolsets`、`/browser`、`/config`、`/cron`、`/paste`、`/image`、`/statusbar`、`/plugins`、`/indicator`、`/redraw`、`/clear`、`/history`、`/copy`、`/billing` 和 `/quit` 是**仅限 CLI** 的命令。
- `/skills` 可用于搜索、浏览和安装技能；写入审批子命令包括 `pending`、`approve`、`reject`、`diff` 和 `approval`。
- `/verbose` 切换工具进度显示模式。
- `/status`、`/version`、`/bg`、`/btw`、`/queue`、`/steer`、`/voice`、`/reload-mcp`、`/reload-skills`、`/rollback`、`/debug`、`/fast`、`/busy`、`/footer`、`/curator`、`/credits`、`/suggestions`、`/blueprint`、`/sessions` 和 `/yolo` 可在 CLI 中使用。

## 破坏性命令的确认提示

CLI 在执行会丢弃未保存会话状态的斜杠命令前会提示确认。当前破坏性命令集为：

| 命令 | 销毁的内容 |
|---------|------------------|
| `/clear` | 清屏并开始新会话——当前会话 ID 和内存中的历史记录将丢失。 |
| `/new` / `/reset` | 开始新会话（新会话 ID + 空历史记录）。 |
| `/undo` | 从历史记录中移除最后一轮用户/助手对话。 |
| `/exit --delete` / `/quit --delete` | 退出**并**永久删除当前会话的 SQLite 历史记录和磁盘上的转录文件。 |

对于上述每个命令，CLI 会打开一个三选项弹窗：**Approve Once**（本次执行）、**Always Approve**（执行并持久化 `approvals.destructive_slash_confirm: false`，使未来的破坏性命令无需提示直接运行），或 **Cancel**。

**内联跳过：** 追加 `now`、`--yes` 或 `-y` 可为单次调用绕过弹窗——例如 `/reset now`、`/new --yes my-session`、`/clear -y`、`/undo -y`。适用于弹窗在你的终端无法正常渲染的情况（见 [issue #30768](https://github.com/NousResearch/hermes-agent/issues/30768)，原生 Windows PowerShell）或对 CLI 进行脚本化操作时。

在 `~/.hermes/config.yaml` 中设置 `approvals.destructive_slash_confirm: false` 可全局禁用提示；设置回 `true` 可重新启用。背景说明见 [安全——破坏性斜杠命令确认](../user-guide/security.md#dangerous-command-approval)。
