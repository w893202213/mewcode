# MewCode 对话 TUI（第一步）Plan

## 架构概览

五个组件，单向依赖：

```
cli（入口装配）
 ├─→ config（YAML 读取/校验/选择）
 ├─→ provider（统一接口 + 两家实现）
 │     ├─ AnthropicProvider → /v1/messages SSE
 │     └─ OpenAIProvider    → /v1/chat/completions SSE
 ├─→ session（对话历史，仅内存）
 └─→ tui（prompt_toolkit 输入 + Rich 渲染）
```

- **CLI 入口**：解析启动参数（`--profile`、`--config`），加载配置、创建 Provider、启动 TUI。启动期错误（配置问题）打印到 stderr 并以非零码退出
- **配置层**：读取 YAML、校验六字段、按 profile 名选择；错误带明确中文信息
- **Provider 层**：抽象基类 `Provider` 定义统一流接口，输出归一化的 `StreamEvent` 序列，TUI 完全不感知协议差异；工厂函数按 `protocol` 分发
- **会话层**：维护对话历史（仅进程内存），不感知任何协议
- **TUI 层**：prompt_toolkit 负责输入（历史回溯、斜杠命令补全），Rich 负责输出（Markdown 渲染、Live 流式逐字、思考块灰色展示、错误红色显示）

**数据流**：用户输入 → 追加进会话 → `Provider.stream(messages)` 产出事件流 → TUI 逐事件渲染 → 流结束把 assistant 回复（含思考块）追加进会话 → 回到输入。

## 核心数据结构

### ProviderConfig（dataclass，`config.py`）
| 字段 | 类型 | 说明 |
|---|---|---|
| name | str | 供应商标识名，必填 |
| protocol | str | `"anthropic"` \| `"openai"`，必填，其他值校验报错 |
| model | str | 模型名，必填 |
| base_url | str | 请求地址，必填 |
| api_key | str | 认证密钥，必填（永不打印回显） |
| thinking | bool | 是否扩展思考，缺省 False |

### Message（dataclass，`session.py`）
| 字段 | 类型 | 说明 |
|---|---|---|
| role | str | `"user"` \| `"assistant"` |
| text | str | 正式回复文本（user 消息即用户输入） |
| thinking | str | 思考内容拼接文本（仅展示用） |
| thinking_raw | list[dict] | 原始思考块 JSON，多轮时由 AnthropicProvider 原样回传（官方硬性要求） |

### StreamEvent（`provider/base.py`，归一化流事件）
```python
class ThinkingDelta: text: str   # 思考增量 → 灰色缩进展示
class TextDelta:     text: str   # 正式回复增量 → 追加 Markdown buffer，Live 刷新
class Done:                      # 流正常结束
```

### Provider（ABC，`provider/base.py`）
```python
class Provider(ABC):
    @abstractmethod
    def stream(self, messages: list[Message]) -> Iterator[StreamEvent]: ...
```
- 同步迭代器：本步流式期间 UI 本就阻塞（不做打断），异步只增复杂度
- 协议转换（Message → 请求体）发生在各 Provider 内部，Session 不感知协议 → 新增后端零改动会话层
- 错误不走事件，抛 `ProviderError`（带友好中文信息），由 TUI 捕获显示

### ChatSession（`session.py`）
```python
class ChatSession:
    messages: list[Message]
    def add_user(self, text: str) -> None
    def add_assistant(self, text: str, thinking: str, thinking_raw: list[dict]) -> None
    def clear(self) -> None
```

## 模块设计

### config.py
**职责**：定位配置文件、读取 YAML、校验、按名选择
**对外接口**：`load_config(path) -> list[ProviderConfig]`（校验失败抛 `ConfigError`，中文信息）；`select_profile(configs, name) -> ProviderConfig`（找不到时报错）
**依赖**：PyYAML

### provider/base.py
**职责**：定义 `Provider` ABC、`StreamEvent` 三个事件、`ProviderError`
**依赖**：无

### provider/anthropic.py
**职责**：Claude Messages API 流式调用与事件解析
**请求**：`POST {base_url}/v1/messages`；头 `x-api-key`、`anthropic-version: 2023-06-01`；体含 `model`、`max_tokens`、`stream: true`、`messages`（assistant 消息按 content 数组组装：text 块 + thinking 块原样回传）；`thinking: true` 时加 `thinking: {type: "enabled", budget_tokens: 4096}`；不设置 temperature（与 thinking 互斥）
**SSE 解析**：`content_block_delta`（`thinking_delta` → ThinkingDelta；`text_delta` → TextDelta）、`message_stop` → Done；流中途 `error` 事件与 HTTP 错误 → ProviderError
**依赖**：httpx、base

### provider/openai.py
**职责**：OpenAI chat/completions 流式调用与事件解析
**请求**：`POST {base_url}/v1/chat/completions`；`Authorization: Bearer`；体含 `model`、`messages`（仅 role+content 文本）、`stream: true`；`thinking: true` 时透传 `"thinking": true`
**SSE 解析**：`choices[0].delta.content` → TextDelta；`[DONE]` / 非空 `finish_reason` → Done；HTTP 错误与 `error` 字段 → ProviderError
**依赖**：httpx、base

### provider/\_\_init\_\_.py
**职责**：注册表 + 工厂
**对外接口**：`_REGISTRY: dict[str, type[Provider]] = {"anthropic": ..., "openai": ...}`；`create_provider(config) -> Provider`（未知协议报错）
新增后端 = 实现子类 + 注册表加一行（N3）

### session.py
**职责**：对话历史内存维护，协议无关

### tui/app.py
**职责**：TUI 主循环与渲染
**输入**：`PromptSession` + `NestedCompleter`（补全 `/exit /clear /help`）+ 默认内存历史（上下键回溯）
**输出**：Rich Console；流式时 `Live(Markdown(buffer))` 逐字刷新；思考块灰色缩进实时展示；欢迎语含当前配置名；错误红色显示
**依赖**：prompt_toolkit、rich、session、provider、commands

### tui/commands.py
**职责**：斜杠命令解析与分发
**对外接口**：`handle_command(text, session, app) -> CommandResult`（继续 / 退出）

### cli.py + \_\_main\_\_.py
**职责**：argparse（`--profile`、`--config`）、装配、启动；ConfigError → stderr + `sys.exit(1)`

## 模块交互

**启动链**：`cli.main()` → `load_config` → `select_profile` → `create_provider` → `tui.run(provider, config)`

**每轮对话**：
1. TUI 读入文本；以 `/` 开头 → commands 处理（`/clear` → session.clear；`/exit` → 退出；`/help` → 打印命令列表）
2. 普通文本 → `session.add_user(text)`（标记本轮起点）
3. `provider.stream(session.messages)` 逐个产出事件：
   - `ThinkingDelta` → 思考区灰色追加
   - `TextDelta` → 追加回复 buffer，`Live` 刷新 Markdown
   - `Done` → `session.add_assistant(text, thinking, thinking_raw)`
4. 抛 `ProviderError` → 红色显示错误；回滚本轮 user 消息（未被处理的输入不留在上下文里），回到输入
5. 回到步骤 1

**错误边界**：`ConfigError` 在 cli 层处理（退出，AC10）；`ProviderError` 在 TUI 层处理（显示不崩溃，AC8）。

## 文件组织

```
mewcode/
├── pyproject.toml           — 项目元数据与依赖（setuptools，pip install -e 安装）
├── mewcode/
│   ├── __init__.py
│   ├── __main__.py          — python -m mewcode 入口
│   ├── cli.py               — argparse、装配、启动
│   ├── config.py            — ProviderConfig、load_config、select_profile、ConfigError
│   ├── session.py           — Message、ChatSession
│   ├── provider/
│   │   ├── __init__.py      — 注册表、create_provider
│   │   ├── base.py          — Provider ABC、StreamEvent、ProviderError
│   │   ├── anthropic.py     — AnthropicProvider
│   │   └── openai.py        — OpenAIProvider
│   └── tui/
│       ├── __init__.py
│       ├── app.py           — 主循环、流式渲染、错误显示
│       └── commands.py      — 斜杠命令
├── tests/
│   ├── test_config.py       — 配置加载/校验/选择
│   ├── test_session.py      — 会话增删清空
│   └── test_providers.py    — 本地 mock SSE 服务，验证两家 Provider 解析
└── docs/                    — spec.md / plan.md / task.md / checklist.md
```

配置文件默认路径 `~/.mewcode/config.yaml`，可用 `--config` 覆盖。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| HTTP 客户端 | httpx（同步） | SSE 逐行迭代简单；本步流式期间 UI 本就阻塞，异步徒增复杂度 |
| SSE 解析 | 手写逐行解析 | 约 30 行，可控；Anthropic 流中途 `error` 事件必须专门处理 |
| TUI 组合 | PromptSession + Rich Live | 输入（历史/补全）与输出（Markdown/流式）各取所长；全屏 App 布局复杂 |
| 协议转换位置 | Provider 内部把 Message → 请求体 | Session 不感知协议，新增后端不动会话层（N3） |
| 思考预算 | 内置常量 4096（官方下限 1024），`max_tokens` 内置 8192 | 配置仅 6 字段无预算项；保证 budget < max_tokens |
| thinking 块回传 | `thinking_raw` 原样存入并回传 | Anthropic 官方要求：thinking blocks 必须原样返回，否则多轮报错 |
| OpenAI reasoning_content | 不解析（流中忽略） | spec 未要求，YAGNI；后续需要再加 |
| 错误传递 | 抛 `ProviderError`，不走事件流 | Pythonic；TUI 捕获显示；失败轮次回滚 user 消息防污染上下文 |
| Python 版本 | 3.10+ | N1 |

## spec 覆盖对照

| spec 需求 | plan 归属 |
|---|---|
| F1 配置加载/选择 | config.py + cli.py（--profile 缺省第一份、--config 路径） |
| F2 交互界面/流式逐字 | tui/app.py（PromptSession + Live(Markdown)） |
| F3 Provider 抽象/两种协议 | provider/（base + anthropic + openai + 注册表） |
| F4 extended thinking | anthropic.py（thinking 参数）+ app.py（思考区样式） |
| F5 OpenAI thinking 透传 | openai.py（请求体透传） |
| F6 多轮对话 | session.py（仅内存） |
| F7 内置命令/历史 | tui/commands.py + PromptSession 内存历史 |
| F8 错误不崩溃 | ProviderError + TUI 红色显示 + 回滚 |
| N3 可扩展性 | 注册表工厂，新增后端一行注册 |
| N4 密钥安全 | api_key 仅存 ProviderConfig，任何打印路径不输出 |
