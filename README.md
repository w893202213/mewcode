# MewCode 🐱

> 用 Python 从零实现的终端 AI 编程助手 —— 自研 Agent Loop、工具系统、五层权限、MCP、记忆与 SubAgent。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/w893202213/mewcode/actions/workflows/ci.yml/badge.svg)](https://github.com/w893202213/mewcode/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-745%20passed-brightgreen.svg)](#testing)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/w893202213/mewcode)

MewCode 是一个类似 Claude Code 的终端编程 Agent：在终端里用自然语言描述任务，它会读取代码、搜索文件、执行命令、修改代码，并在每一步有副作用的操作前向你确认。项目不依赖 LangChain 等 Agent 框架，核心循环、工具协议、权限闸门、上下文管理、MCP 客户端与记忆系统全部从零实现。

## Contents

- [Demo](#demo)
- [Try Online](#try-online)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Advanced Configuration](#advanced-configuration)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Design Docs](#design-docs)
- [Engineering Metrics](#engineering-metrics)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Demo

![MewCode 演示](docs/assets/demo.gif)

> 一次真实会话：写文件 → 权限确认 → 执行 → 总结。

<details>
<summary>展开查看交互示例</summary>

```text
❯ 写一个 fizzbuzz.py，打印 1 到 15 的 FizzBuzz，然后运行它

🐱 MewCode
第 1 轮
🔧 write_file {"file_path": "fizzbuzz.py", "content": "..."}

⚠ 需要权限确认
工具: write_file
原因: 默认模式：有副作用的工具需要人工确认
  [y] 本次允许  [s] 本会话允许  [a] 永久允许  [n] 拒绝 > s

✅ 已写入 fizzbuzz.py（309 字节）

第 2 轮
🔧 run_command {"command": "python3 fizzbuzz.py"}

⚠ 需要权限确认
工具: run_command
原因: 默认模式：有副作用的工具需要人工确认
  [y] 本次允许  [s] 本会话允许  [a] 永久允许  [n] 拒绝 > s

1
2
Fizz
4
Buzz
...

已完成：fizzbuzz.py 写好了，运行输出符合预期。
```

</details>

---

## Try Online

MewCode 是终端程序，在线体验通过 **GitHub Codespaces** 提供：在浏览器里启动一个隔离的云端终端，不用本地安装。

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/w893202213/mewcode)

1. 点击上面的徽章（需要 GitHub 账号），等待环境创建完成（首次约 1–2 分钟）。
2. 在终端里运行配置向导（DeepSeek 选 1，Anthropic 选 2），填入你自己的 API key：

   ```bash
   bash scripts/setup-codespace-config.sh
   ```

3. 启动 MewCode：

   ```bash
   python -m mewcode
   ```

   （如果 `mewcode` 命令已在 PATH 中，也可以直接运行 `mewcode`。）

4. 试试输入：`用 Python 写一个快速排序并运行测试`。

> Codespaces 环境是隔离的，闲置后会自动停止；API key 由访客自备，不会消耗项目作者的额度。
> 获取 key：[DeepSeek](https://platform.deepseek.com/api_keys) · [Anthropic](https://console.anthropic.com/settings/keys)

### Can't Paste the API Key?

Codespaces 的终端运行在浏览器里，按 `Ctrl+V` 时 VS Code Web 会调用浏览器的剪贴板读取接口；如果浏览器没有授予该权限，就会提示「无法从浏览器的剪贴板中读取」。这不是 MewCode 的问题，可以：

- 在浏览器地址栏左侧的站点设置里允许 **Clipboard / 剪贴板读取**，然后刷新页面；
- 或用右键 → Paste（部分浏览器走原生粘贴事件，不受该权限限制）；
- 或完全绕开终端粘贴：在左侧文件树新建 `mewcode-key.txt`，把 key 粘贴进去并保存（编辑器的粘贴不受终端限制），然后运行：

  ```bash
  bash scripts/setup-codespace-config.sh --provider deepseek --key-file mewcode-key.txt --non-interactive
  ```

  脚本写入 `~/.mewcode/config.yaml` 后会自动删除 `mewcode-key.txt`。用 Anthropic 时把 `--provider` 换成 `anthropic`。
- 也可以把 key 存成 Codespaces Secret：GitHub → **Settings → Codespaces → Secrets** → 新建 `MEWCODE_API_KEY`，然后运行 `bash scripts/setup-codespace-config.sh --provider deepseek --non-interactive`，脚本会直接读取该环境变量。

---

## Features

| 模块 | 能力 |
|---|---|
| **Agent 内核** | 流式 ReAct 循环、多轮工具调用、工具结果回灌、中断与恢复、token 用量统计 |
| **工具系统** | 7 个内置工具：`read_file` / `write_file` / `edit_file` / `run_command` / `grep_code` / `find_files` / `agent`；工具注册表统一 schema 与执行入口 |
| **五层权限** | 黑名单硬拦 → 路径沙箱 → 可配置规则 → 权限模式兜底 → 人在回路确认；拒绝原因回灌模型，循环不中断 |
| **双协议 Provider** | Anthropic Messages 与 OpenAI Chat Completions 双协议，流式 SSE、Anthropic extended thinking、OpenAI 兼容服务的 `thinking` 参数透传、`context_window` 配置化 |
| **MCP 客户端** | stdio 子进程与 Streamable HTTP 两种传输；启动时 `tools/list` 动态发现工具；单个 Server 握手失败自动隔离 |
| **上下文管理** | 工具结果超阈值落盘只留预览；历史逼近窗口上限时 LLM 结构化摘要兜底；连续失败 3 次熔断 |
| **记忆系统** | 三层指令文件、JSONL 会话存档与 `--resume`、LLM 异步笔记、索引注入与 `recall_memory` 渐进式披露 |
| **技能系统** | `SKILL.md` + frontmatter；内置/用户/项目三级发现；`commit` / `review` / `test` 内置技能；独立会话执行 |
| **Hook 系统** | 10 个生命周期事件；精确/反向/正则/glob 条件组合；shell / 注入提示词 / HTTP / 子 Agent 四种动作；Hook 失败不阻断主流程 |
| **SubAgent** | 定义式角色 + Fork 式继承两条路径；独立上下文/权限/工具集；后台任务与 `/tasks` 管理；三层工具过滤防无限嵌套 |
| **终端 UI** | prompt_toolkit + rich；流式 Markdown 渲染、输入补全、双击 Ctrl+C 退出、计划模式、权限确认面板 |
| **斜杠命令** | `/help` `/compact` `/clear` `/plan` `/do` `/session` `/memory` `/permission` `/status` `/resume` `/skill` `/tasks` |

---

## Architecture

```mermaid
flowchart TD
    U[用户输入] --> TUI[TUI<br/>prompt_toolkit + rich]
    TUI --> CMD[斜杠命令]
    TUI --> LOOP[Agent Loop<br/>ReAct]

    LOOP --> P[Provider<br/>Anthropic / OpenAI]
    LOOP --> EXEC[Tool Executor]
    LOOP --> EVT[事件流<br/>TextDelta / ToolCall / Usage]
    EVT --> TUI

    EXEC --> GATE[权限闸门]
    GATE --> B1[黑名单]
    GATE --> B2[路径沙箱]
    GATE --> B3[规则引擎]
    GATE --> B4[权限模式]
    GATE --> B5[人工确认]
    GATE --> TOOLS[内置工具]
    GATE --> MCP[MCP 工具]
    GATE --> SUB[SubAgent]

    LOOP --> CTX[上下文管理<br/>估算 / 压缩 / 会话]
    LOOP --> MEM[记忆系统<br/>指令 / 笔记 / 召回]
    LOOP --> HOOK[Hook 引擎]
```

**一次请求的路径**：TUI 收集输入 → Agent Loop 组装系统提示与历史 → Provider 流式返回文本/工具调用 → 有工具调用则交给执行器，先过权限闸门再执行，结果回灌 → 循环直到模型不再调用工具 → TUI 渲染最终回复并持久化会话。

---

## Quick Start

### 1. Installation

```bash
git clone https://github.com/w893202213/mewcode.git
cd mewcode

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

要求 Python 3.10+。

### 2. Configure a Model

MewCode 支持 Anthropic 与 OpenAI 兼容协议。复制示例配置并填入自己的 key：

```bash
mkdir -p ~/.mewcode
cp config.example.yaml ~/.mewcode/config.yaml
chmod 600 ~/.mewcode/config.yaml
```

最小配置（以 DeepSeek 为例）：

```yaml
providers:
  - name: deepseek
    protocol: openai
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: sk-your-key          # 不要提交到 git
    context_window: 65536         # 可选，缺省 200000
```

也可以直接用 Anthropic：

```yaml
providers:
  - name: claude
    protocol: anthropic
    model: claude-sonnet-4-5
    base_url: https://api.anthropic.com
    api_key: sk-ant-your-key
    thinking: true                # 可选，启用扩展思考
```

多份 provider 配置可以用 `--profile` 切换：

```bash
mewcode --profile deepseek
mewcode --config /path/to/config.yaml
```

### 3. Run

```bash
mewcode
```

```text
╭──────────────────────── MewCode v0.1.0 ────────────────────────╮
│ 配置: deepseek（协议 openai）                                   │
│ 模型: deepseek-chat                                             │
│ 权限模式: 默认（default，/permission 切换）                     │
│ 输入 /help 查看可用命令。                                       │
╰─────────────────────────────────────────────────────────────────╯
❯
```

恢复上一次会话：

```bash
mewcode --resume            # 恢复最近一个会话
mewcode --resume <session_id>
```

### 4. Docker (Optional)

```bash
docker compose build
docker compose run --rm mewcode
```

`compose.yaml` 会把 `runtime/user` 挂到容器内 `~/.mewcode`、`runtime/workspace` 挂到 `/workspace`。首次使用前把配置文件放到 `runtime/user/config.yaml`。

---

## Usage

### Natural-Language Tasks

```text
❯ 找出这个项目里所有 TODO，按文件分组，并挑一个最容易修的修掉

🐱 MewCode
第 1 轮
🔧 grep_code  {"pattern": "TODO", "path": "."}
...
🔧 read_file  {"path": "mewcode/tools/base.py"}
...
🔧 edit_file  {"path": "...", "old_text": "...", "new_text": "..."}

╭──────────────────────── ⚠ 需要权限确认 ────────────────────────╮
│ 工具: edit_file                                                 │
│ 原因: 默认模式：有副作用的工具需要人工确认                      │
╰─────────────────────────────────────────────────────────────────╯
  [y] 本次允许  [s] 本会话允许  [a] 永久允许  [n] 拒绝 >
```

### Permission Modes

| 模式 | 行为 |
|---|---|
| `strict` | 只读工具也需要确认 |
| `default` | 只读放行，有副作用的工具询问（推荐） |
| `accept` | 未命中规则的操作一律放行；黑名单/沙箱/deny 规则仍生效 |

用 `/permission` 循环切换，或在配置文件的 `permissions` 段声明规则。

### Plan Mode

```text
❯ /plan
已进入计划模式：只有只读工具可用

❯ 分析一下这个项目的权限系统是怎么设计的
...

❯ /do
已退出计划模式
```

### Slash Commands

| 命令 | 作用 |
|---|---|
| `/help` | 帮助与命令列表 |
| `/compact` | 立即压缩上下文 |
| `/clear` | 清空对话，开始新会话 |
| `/plan` / `/do` | 进入 / 退出计划模式 |
| `/session` | 查看当前会话信息 |
| `/memory` | 查看记忆系统状态 |
| `/permission` | 切换权限档位 |
| `/status` | 系统状态与 token 用量 |
| `/resume` | 列出最近会话并恢复 |
| `/skill` | 列出 / 使用技能 |
| `/tasks` | 查看后台子 Agent 任务 |
| `/exit` | 退出 |

---

## Advanced Configuration

### MCP Server

stdin/stdout 子进程：

```yaml
mcp_servers:
  demo:
    command: python
    args: [scripts/mock_mcp_server.py]
    env:
      MOCK_PAGE_SIZE: "${MOCK_PAGE_SIZE}"   # 支持 ${VAR} 展开
```

Streamable HTTP：

```yaml
mcp_servers:
  context7:
    url: https://mcp.context7.com/mcp
    headers:
      Authorization: "Bearer ${CONTEXT7_TOKEN}"
```

用户级配置（`~/.mewcode/config.yaml`）与项目级配置（`.mewcode/config.yaml`）按键合并，同名条目整条覆盖。MewCode 在启动握手时调用 `tools/list` 动态拉取工具，单个 Server 失败会隔离跳过，不影响其他工具。

### Hook

```yaml
hooks:
  - event: tool_before
    if:
      all:
        - field: tool
          pattern: run_command
        - field: args.command
          pattern: /rm -rf/
    action:
      type: command
      run: 'echo "危险删除已被 Hook 拦截"; exit 1'
```

- 事件：`session_start` / `session_end` / `round_start` / `round_end` / `user_message` / `assistant_message` / `tool_before` / `tool_after` / `startup` / `shutdown`
- 条件：精确、反向、正则、glob；`all` / `any` 组合
- 动作：`command` / `inject` / `http` / `agent`
- `tool_before` 上的非零退出或非 2xx 会作为拒绝原因回灌给模型

### SubAgent Roles

在 `.mewcode/agents/` 下放一个 Markdown + frontmatter 即可定义角色：

```markdown
---
name: code-reviewer
description: 代码审查：找 bug 与简化机会
tools: [read_file, grep_code, find_files, run_command]
permission: accept
---

你是 MewCode 的代码审查子 Agent。按严重程度输出发现列表。
```

主 Agent 通过 `agent` 工具调用：`defined` 角色使用独立空白上下文，`fork` 继承父对话并利用 prompt cache。

---

## Project Structure

```text
mewcode/
├── .devcontainer/          # Codespaces 开发容器
├── .github/workflows/      # CI（pytest）
├── .mewcode/agents/        # 项目级子 Agent 角色
├── mewcode/
│   ├── agent/              # ReAct 循环、事件流、工具执行器
│   ├── agents/             # SubAgent 角色发现、管理器、上下文隔离
│   ├── commands/           # 斜杠命令注册与分发
│   ├── context/            # token 估算、上下文压缩、会话存储
│   ├── hooks/              # 生命周期事件、条件匹配、四种动作
│   ├── mcp/                # MCP 配置、协议、stdio/HTTP 传输、工具适配
│   ├── memory/             # 指令文件、会话存档、笔记、回忆工具
│   ├── permissions/        # 黑名单、沙箱、规则、权限闸门
│   ├── prompt/             # 系统提示模块化组装
│   ├── provider/           # Anthropic / OpenAI 协议与流式解析
│   ├── skills/             # 技能发现、加载、执行
│   ├── tools/              # 内置工具与注册表
│   └── tui/                # prompt_toolkit + rich 终端界面
├── docs/
│   ├── assets/             # README 演示 GIF
│   └── ch01..ch17/         # 每章 spec / plan / task / checklist
├── scripts/                # Codespaces 配置向导等
├── tests/                  # 62 个测试文件，745 个用例
├── config.example.yaml
├── Dockerfile
├── compose.yaml
└── pyproject.toml
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

```text
745 passed in 19.19s
```

测试覆盖 Agent 循环、工具执行、权限五层、MCP 协议与传输、上下文压缩、记忆召回、Hook 条件与动作、SubAgent 生命周期、TUI 关键路径等。

## Design Docs

项目采用 spec-driven 开发：每个功能先写四份文档，再实现与验收。

```text
docs/chXX/
├── spec.md        # 做什么：背景、目标、功能需求
├── plan.md        # 怎么做：架构决策、模块划分
├── task.md        # 按什么顺序做
└── checklist.md   # 做对了没：行为级验收项
```

17 章覆盖：TUI 对话 → 工具系统 → Agent Loop → 系统提示 → 五层权限 → MCP → 上下文管理 → 记忆 → 斜杠命令 → 技能 → Hook → SubAgent → 记忆渐进式披露。

## Engineering Metrics

| 指标 | 数值 |
|---|---|
| 核心 Python 代码 | 9,809 行 |
| 测试代码 | 10,736 行 |
| 测试用例 | 745 个，全部通过 |
| 设计文档 | 17 章、68 个 Markdown 文件 |
| 内置工具 | 7 个 |
| Hook 事件 | 10 个 |

## Troubleshooting

| 现象 | 原因 | 解决 |
|---|---|---|
| `配置错误: ... unacceptable character #x001b` | 粘贴的 API key 带入了终端转义序列（ESC） | 用 `--key-file` 流程，或重新复制纯文本 key；配置脚本会清洗控制字符 |
| `鉴权失败：api_key 无效或无权限（HTTP 401）` | key 无效/过期，或协议与服务商不匹配 | 检查 key 和 `base_url`；DeepSeek 使用 `protocol: openai` |
| `protocol: anthropic` 配 `api.deepseek.com` | DeepSeek 不是 Anthropic 协议 | 改用 `protocol: openai`，或运行 `--provider deepseek` |
| `mewcode: command not found` | 控制台脚本不在 PATH | 使用 `python -m mewcode`，或重新 `pip install -e ".[dev]"` |
| 「无法从浏览器的剪贴板中读取」 | 浏览器未授予剪贴板读取权限 | 授权/右键粘贴，或用 `--key-file`（见 Try Online） |
| Codespace 首次启动较慢 | 容器构建 + 依赖安装 | 等待 1–2 分钟；后续启动会快很多 |
| `ModuleNotFoundError: No module named 'mewcode'` | 项目未安装 | 在仓库根目录运行 `pip install -e ".[dev]"` |

> 配置文件位置：本地为 `~/.mewcode/config.yaml`；Codespaces 中为 `/home/vscode/.mewcode/config.yaml`。

## License

[MIT](LICENSE)
