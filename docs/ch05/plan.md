# Agent Loop Plan

## 架构概览

在 ch04 三层分工上扩展，agent/ 包从「单个工具的执行器」升级为「Agent Loop 主体」：

1. **agent/events.py（新增）**：Agent 事件流类型。复用 provider 的 `TextDelta`/`ThinkingDelta`（界面已认识它们），新增工具调用开始、工具结果、Token 用量、进度、轮次结束五类事件。TUI 只依赖 `AgentEvent` 联合类型，Agent 完全不感知界面。
2. **agent/collector.py（新增）**：流式双路收集器——文本增量一边转发（Agent 直接把事件 yield 给事件流），一边累积完整响应供落盘。
3. **agent/executor.py（新增）**：`execute_call` 纯执行函数，取代 ch04 的 ToolRunner——执行与回灌解耦（回灌顺序必须由 Agent 统一控制，见 N7）。不感知会话、不感知协议。
4. **agent/loop.py（新增）**：`Agent` 循环主体。异步生成器 `run()`：逐轮调 LLM（流经泵线程桥入 asyncio）→ 双路收集 → 落盘 assistant 消息 → 分批执行工具 → 按原始顺序回灌 → 检查五种停止条件。持有 Plan Mode 状态与取消标志。
5. **agent/bridge.py（新增）**：`run_in_background(agent, text)`——后台线程跑 `asyncio.run` 把 Agent 事件搬进 `queue.Queue`，TUI 主线程同步消费。Agent 内部纯 asyncio，测试可 `async for` 直接消费，不碰线程。
6. **provider/（修改）**：`Done` 携带 usage（Anthropic 解析 `message_delta` 的 usage；OpenAI 请求体加 `stream_options.include_usage` 并解析最终 chunk）。协议层保持同步迭代器不动。
7. **tools/（修改）**：`Tool` 增加 `read_only` 标记（默认 True；写/改/命令工具显式 False）——Plan Mode 过滤与分批并发判定共用。
8. **TUI（修改）**：`_chat_round` 换成事件消费循环：每轮一个 Live 渲染流式文本，工具调用/结果沿用 ch04 面板样式，结束显示 token 用量与停止原因面板。

## 核心数据结构

```python
# agent/events.py
@dataclass
class ToolCallStarted:
    call: ToolCall                     # 工具即将执行（TUI 展示工具名+参数）

@dataclass
class ToolResultEvent:
    call: ToolCall
    result: ToolResult                 # 执行结果（TUI 展示结果面板）

@dataclass
class UsageEvent:
    input_tokens: int                  # 每次 LLM 调用的用量（TUI 累计展示）
    output_tokens: int

@dataclass
class ProgressEvent:
    iteration: int                     # 第 N 轮（从 1 起，TUI 显示进度）

class RoundEndReason(Enum):
    OK = "ok"                          # 模型不再调工具，正常结束
    LIMIT = "limit"                    # 达迭代上限
    CANCELLED = "cancelled"            # 用户取消
    UNKNOWN_TOOL = "unknown_tool"      # 连续未知工具
    ERROR = "error"                    # 流出错（已回滚本轮）

@dataclass
class RoundEnd:
    reason: RoundEndReason
    message: str = ""                  # 中文说明（错误原因/取消提示/上限提示）

AgentEvent = (TextDelta | ThinkingDelta | ToolCallStarted | ToolResultEvent
              | UsageEvent | ProgressEvent | RoundEnd)
```

```python
# agent/collector.py
class StreamCollector:
    """双路收集：增量实时转发给事件流，同时攒出完整响应。"""
    def add_text(self, text: str) -> None
    def add_thinking(self, text: str) -> None
    def set_thinking_raw(self, blocks: list[dict]) -> None
    @property
    def full_text(self) -> str           # 落盘用完整文本
    @property
    def thinking_text(self) -> str
    @property
    def thinking_raw(self) -> list[dict]
```

```python
# agent/executor.py
def execute_call(registry: ToolRegistry, call: ToolCall) -> ToolResult:
    """执行单个工具调用，不落盘。parse_error/未知工具/执行失败一律返回失败结果。"""
```

```python
# agent/loop.py
MAX_ITERATIONS = 30
UNKNOWN_TOOL_LIMIT = 2  # 连续未知工具上限

PLAN_MODE_ON_MESSAGE = "（Plan Mode 已开启：只能使用只读工具调研，请给出计划，不要修改文件或执行命令。）"
PLAN_MODE_OFF_MESSAGE = "（Plan Mode 已关闭：已切回执行模式，可以按计划动手了。）"

class Agent:
    def __init__(self, provider: Provider, registry: ToolRegistry,
                 session: ChatSession, max_iterations: int = MAX_ITERATIONS) -> None
    def cancel(self) -> None                      # 线程安全：请求停止循环
    def enter_plan_mode(self) -> None             # 置标志 + 注入提示 user 消息（幂等）
    def exit_plan_mode(self) -> None              # 清标志 + 注入提示 user 消息（幂等）
    @property
    def plan_mode(self) -> bool
    def _visible_tools(self) -> list[Tool]        # plan 模式只保留 read_only 工具
    async def run(self, user_text: str) -> AsyncIterator[AgentEvent]

# agent/bridge.py
def run_in_background(agent: Agent, text: str) -> queue.Queue[AgentEvent]:
    """后台线程跑 asyncio.run，Agent 事件经线程安全队列交给主线程。"""
```

```python
# provider/base.py（改）
@dataclass
class UsageInfo:
    input_tokens: int
    output_tokens: int

@dataclass
class Done:
    thinking_raw: list[dict] = field(default_factory=list)
    usage: UsageInfo | None = None     # 本轮调用的 token 用量（F8）

# tools/base.py（改）
class Tool(ABC):
    ...
    read_only: ClassVar[bool] = True   # 只读标记：plan 模式过滤 + 分批并发判定
```

## 模块设计

### 模块 1：agent/events.py — 事件类型
**职责：** `AgentEvent` 联合类型与各事件数据类定义（见核心数据结构）。**依赖：** provider.base（复用 TextDelta/ThinkingDelta/ToolCall）、tools.base（ToolResult）。**对外接口：** 各 dataclass + `AgentEvent` + `RoundEndReason`。

### 模块 2：agent/collector.py — 流式双路收集器
**职责：** 每轮 LLM 流期间收集文本/思考增量，供实时转发与落盘共用。**依赖：** 无。**对外接口：** `StreamCollector`。使用方式：`run()` 收到 TextDelta 时 `collector.add_text()` 且 `yield` 同一事件——转发与累积同源，保证「界面看到的 = 落盘的」（AC4）。

### 模块 3：agent/executor.py — 单工具执行
**职责：** `execute_call(registry, call) -> ToolResult`：
- `call.parse_error` 非空 → 失败结果（同 ch04）
- `registry.get(call.name)` 失败（未知工具）→ 失败结果（含已注册工具列表）
- 否则 `run_tool(tool, call.args)`（超时/异常/截断包装不变，N5）
- **不弹确认**（F6：循环中命令直接执行）；**不写会话**（回灌是 Agent 的职责）
**依赖：** tools.base、tools.registry。替代 ch04 的 `agent/runner.py`（ToolRunner 删除，确认回调机制随删——`Tool.requires_confirmation` 标记保留待后续安全章节）。

### 模块 4：agent/loop.py — Agent 循环主体
**职责：** 见 `Agent` 接口。核心流程（`run()` 主体）：

```
记录 start_index → session.add_user(text) → 清取消标志
for iteration in 1..MAX_ITERATIONS:
    yield ProgressEvent(iteration)
    collector = StreamCollector(); calls = []
    async for ev in _stream_bridged(provider, messages, _visible_tools()):
        [取消检查]（流中逐事件；用 wait_for 轮询让取消能打断等待中的流）
        TextDelta/ThinkingDelta → collector 累积 + yield 转发（双路）
        ToolCall → calls 收集；Done → thinking_raw + usage 记下
    session.add_assistant(collector.full_text, thinking, thinking_raw, tool_uses)
    usage 累计 → yield UsageEvent
    calls 为空 → yield RoundEnd(OK) → 结束
    分批执行（F5）→ 按原始顺序回灌 → 逐条 yield ToolResultEvent
    未知工具连续计数 ≥ UNKNOWN_TOOL_LIMIT → yield RoundEnd(UNKNOWN_TOOL) → 结束
yield RoundEnd(LIMIT)（循环走完未结束）
异常：ProviderError → 回滚 start_index → RoundEnd(ERROR, 中文错误)
      其他 Exception → 同样回滚 + RoundEnd(ERROR, f"内部错误: …")（兜底保证界面不挂起）
```

`_stream_bridged`：私有异步辅助——专用线程跑同步 `provider.stream()`，事件经 `call_soon_threadsafe` 灌入 `asyncio.Queue`；Agent `async for` 消费。Provider 层零改动即可接入 asyncio 世界；取消 = 停止消费（泵线程 daemon 自然结束）。**依赖：** provider、session、collector、executor、events。

### 模块 5：agent/bridge.py — 线程桥
**职责：** `run_in_background(agent, text) -> queue.Queue`：起 daemon 线程跑 `asyncio.run(_pump(...))`，`_pump` `async for` 消费 `agent.run(text)` 并把每个事件 `q.put`。**依赖：** agent.loop。仅 TUI 使用；测试直接 `async for agent.run(...)`。

### 模块 6：provider/base.py（修改）
**职责：** `Done` 增加 `usage: UsageInfo | None`；新增 `UsageInfo`。**依赖：** 无变化。

### 模块 7：provider/anthropic.py（修改）
- `_iter_events` 解析 `message_delta`：`usage.input_tokens`/`output_tokens`（缺省 0），`message_stop` 时随 Done 带出。
- 其余不动（请求体、工具块解析、回放重建已是多工具块通用）。

### 模块 8：provider/openai.py（修改）
- `_build_payload` 增加 `"stream_options": {"include_usage": True}`。
- `_iter_events`：`choices` 为空但带 `usage` 的最终 chunk（以及 finish chunk 上附带的 usage）解析 `prompt_tokens`/`completion_tokens`，随 Done 带出；无 usage 时为 None。

### 模块 9：tools/base.py + files.py + shell.py（修改）
- `Tool` 增加 `read_only: ClassVar[bool] = True`（默认只读）。
- `WriteFileTool`/`EditFileTool`/`RunCommandTool` 显式 `read_only = False`；三个读工具维持默认。

### 模块 10：tui/app.py（修改）
- `run()`：`session = ChatSession()` → `agent = Agent(provider, registry, session)`；命令分发传入 agent；`_chat_round` 替换为 `_agent_round`。
- `_agent_round(console, agent, text, tracker)`：
  - `q = run_in_background(agent, text)`；循环 `q.get(timeout=0.1)` 消费事件：
  - `ProgressEvent` → 开启新一轮 Live（渲染「第 N 轮」+ thinking 计时 + 流式 Markdown，`_stream_renderable` 扩展 iteration 参数）
  - `TextDelta`/`ThinkingDelta` → 累积 + `live.update`
  - `ToolCallStarted` → 关闭本轮 Live，打印 `🔧 工具名 + 参数摘要`（ch04 样式）
  - `ToolResultEvent` → 打印结果面板（成功绿/失败红，2000 字符显示截断）
  - `UsageEvent` → 累加 input/output
  - `RoundEnd` → 关闭 Live；打印 token 用量行（有 usage 时，dim 样式）；按 reason 打印面板：ERROR→红色「请求失败」、CANCELLED→黄色「已取消（已完成步骤保留）」、LIMIT→黄色「已达迭代上限（30 轮）」、UNKNOWN_TOOL→黄色「连续调用未知工具，已停止」、OK→无面板
  - `KeyboardInterrupt` → `agent.cancel()` → 继续排空队列直到 RoundEnd（取消中断等待 ≤100ms）→ `tracker.reset()`
- 双击 Ctrl+C 退出、/exit /clear /help、欢迎语不动；确认 PromptSession 与 confirm 回调随 ToolRunner 一并删除（F6 循环不确认；后续安全章节将重新设计确认机制）。

### 模块 11：tui/commands.py（修改）
- `handle_command(text, session, agent)`：`/plan` → `agent.enter_plan_mode()`，反馈「已进入计划模式（只读工具）……」；`/do` → `agent.exit_plan_mode()`，反馈「已切回执行模式」；重复执行幂等提示。`COMMAND_NAMES` 与 `/help` 文本更新。

### 模块 12：scripts/mock_llm.py（修改）
- `loop-model`（两家协议）：按请求体中已回灌的工具结果数量分轮——第 1 轮返回文本+read_file 调用（分片）、第 2 轮返回 edit_file 调用、第 3 轮返回纯文本最终回复；message_delta / usage chunk 携带 usage。
- `multi-tool-model`：单轮一个响应含 read_file + grep_code + run_command 三个工具调用（Anthropic 三个 tool_use 块 / OpenAI 三个 index）。
- `unknown-tool-model`：每轮调用不存在的 `no_such_tool`（连续两轮触发停止条件）。
- `never-stop-model`：每轮调用 read_file（验证迭代上限）。
- 现有 tool-model / tool-cmd-model / error-model 等流保持不变。

### 模块 13：tests — 测试
- `test_collector.py`（新）：累积/转发两路一致。
- `test_executor.py`（新，替代并删除 `test_runner.py`）：parse_error、未知工具、正常执行、异常转失败；确认拦截测试改写为「`requires_confirmation` 标记存在」断言。
- `test_agent_loop.py`（新，核心）：用 FakeProvider（按脚本产出事件序列）+ 真实 registry 或 fake 工具，覆盖：多轮推进至正常结束、中间轮文本落盘、五种停止条件、多工具分批（fake 工具记录起止时间与线程 ID，断言只读并发重叠+副作用串行+回灌原始顺序）、取消（慢速 fake provider + `agent.cancel()`）、usage 累计、ProviderError 回滚。
- `test_plan_mode.py`（新）：enter/exit 幂等与提示注入、`_visible_tools` 收窄为三个只读工具。
- `test_provider_tools.py`（改）：Anthropic message_delta usage → Done.usage；OpenAI usage chunk → Done.usage。
- `test_providers.py`（改）：OpenAI 请求体含 `stream_options.include_usage`。
- `test_tools.py`（改）：六个工具的 read_only 标记断言。

## 模块交互

```
用户输入 text
  → tui._agent_round: q = run_in_background(agent, text)      [bridge 起后台线程]
  → 后台线程: asyncio.run(_pump) → async for ev in agent.run(text) → q.put(ev)
  → 主线程: q.get 循环渲染（Live / 工具面板 / 结束面板）

agent.run(text) 内部（后台线程的 asyncio 循环）:
  start_index = len(session.messages)
  session.add_user(text)
  for i in 1..30:
      [取消?] → RoundEnd(CANCELLED)
      yield ProgressEvent(i)
      collector = StreamCollector(); calls = []
      泵线程: provider.stream(session.messages, _visible_tools())
              └─ 事件经 call_soon_threadsafe → asyncio.Queue
      async for ev in 队列:
          取消? → 停止消费（本轮部分响应丢弃，不落盘）
          TextDelta/ThinkingDelta → collector 累积 + yield（双路转发）
          ToolCall → calls.append；Done → thinking_raw/usage
      session.add_assistant(full_text, thinking, thinking_raw, tool_uses=calls)
      yield UsageEvent(累计)
      calls 为空 → RoundEnd(OK)
      分批执行:
          只读组: 逐个 yield ToolCallStarted
                  gather(asyncio.to_thread(execute_call, c) for c in 只读组)
          副作用组: 逐个 yield ToolCallStarted → to_thread(execute_call, c)
          [取消?] → RoundEnd(CANCELLED)（未回灌的结果丢弃）
          按原始调用顺序: add_tool_result + yield ToolResultEvent（N7）
      连续未知工具 ≥ 2 → RoundEnd(UNKNOWN_TOOL)
  → RoundEnd(LIMIT)

异常路径:
  ProviderError → del session.messages[start_index:] → RoundEnd(ERROR, str(e))
  其他 Exception → 同样回滚 → RoundEnd(ERROR, "内部错误: …")     [兜底不挂界面]

TUI 侧取消路径:
  Ctrl+C（主线程 KeyboardInterrupt）→ agent.cancel()
  → Agent 在下一事件/等待点察觉（wait_for 轮询 ≤100ms）
  → 停止消费、丢弃进行中轮次 → RoundEnd(CANCELLED) → 面板 + tracker.reset()
```

**取消语义**（对应 AC2③）：已完整落盘的轮次保留在会话；进行中的轮次（未落盘的流式响应、未回灌的工具结果）丢弃；取消后 Agent 不再写会话（避免与下一轮输入竞争）。

**Plan Mode 流程**：`/plan` → `enter_plan_mode()`（置标志 + 注入提示 user 消息）→ 用户提问 → 循环只用 3 个只读工具 → 模型输出计划文本 → RoundEnd(OK)，模式保持 → 用户 review 后输入 `/do` → `exit_plan_mode()`（清标志 + 注入提示）→ 继续执行。

## 文件组织

```
mewcode/
├── agent/
│   ├── __init__.py        — 导出 Agent
│   ├── events.py          — 新增：AgentEvent 事件类型、RoundEndReason
│   ├── collector.py       — 新增：StreamCollector 双路收集
│   ├── executor.py        — 新增：execute_call 纯执行
│   ├── loop.py            — 新增：Agent 循环主体、_stream_bridged、Plan Mode 常量
│   ├── bridge.py          — 新增：run_in_background 线程桥
│   └── runner.py          — 删除（ToolRunner 被 executor + Agent 取代）
├── provider/
│   ├── base.py            — 改：UsageInfo；Done.usage
│   ├── anthropic.py       — 改：message_delta usage 解析
│   ├── openai.py          — 改：stream_options + usage chunk 解析
│   └── __init__.py        — 不变
├── tools/
│   ├── base.py            — 改：Tool.read_only 标记
│   ├── files.py           — 改：WriteFileTool/EditFileTool read_only=False
│   ├── search.py          — 不变（默认只读）
│   ├── shell.py           — 改：RunCommandTool read_only=False
│   ├── registry.py        — 不变
│   └── __init__.py        — 不变
├── session.py             — 不变（add_assistant/add_tool_result 已满足）
├── tui/
│   ├── app.py             — 改：Agent 装配、_agent_round 事件消费渲染
│   └── commands.py        — 改：/plan /do、帮助文本
└── cli.py                 — 不变（Agent 在 tui.run 内装配）

tests/
├── test_collector.py      — 新增
├── test_executor.py       — 新增（test_runner.py 改写后删除）
├── test_agent_loop.py     — 新增：核心循环（FakeProvider + 真实/伪工具）
├── test_plan_mode.py      — 新增
├── test_provider_tools.py — 改：两家 usage 解析
├── test_providers.py      — 改：OpenAI stream_options 断言
└── test_tools.py          — 改：read_only 标记断言

scripts/
└── mock_llm.py            — 改：loop-model / multi-tool-model / unknown-tool-model /
                             never-stop-model + usage 流
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 事件流形态 | `Agent.run()` 为异步生成器（`AsyncIterator[AgentEvent]`） | 测试可 `async for` 直接消费（N6），不碰线程；bridge 只做「异步流 → 同步队列」的搬运 |
| Provider 异步化 | 不改协议层：每轮流在专用泵线程跑同步 `stream()`，事件经 `call_soon_threadsafe` 灌入 `asyncio.Queue` | 两家 provider、mock 脚本、既有解析测试全部零改动；Agent 内部获得真异步 |
| 取消实现 | `threading.Event` 标志 + `asyncio.wait_for` 轮询打断等待 | HTTP 请求本身不可打断；轮询让取消在 ≤100ms 内生效（即使流在等待中）；泵线程 daemon 自然结束 |
| 取消语义 | 已落盘轮次保留、进行中轮次丢弃；取消后不再写会话 | 与「已执行步骤保留」决策一致（丢弃的是未完成轮次）；避免与下一轮输入竞争写会话 |
| ToolRunner 去留 | 删除，拆成 `execute_call`（纯执行）+ Agent 统一回灌 | N7 要求回灌按原始顺序，执行与落盘必须解耦；确认回调机制随删（F6 已定循环不确认），`requires_confirmation` 标记保留 |
| 回灌顺序 | 整批执行完，按模型原始调用顺序统一 `add_tool_result` + 发 ToolResultEvent | 满足 N7：模型看到的工具结果顺序 = 调用顺序；并发完成顺序不影响 |
| 并发实现 | `asyncio.to_thread` + `asyncio.gather` | 标准库；每个工具仍走 `run_tool` 独立超时/截断包装（N5 不变） |
| 未知工具判定 | 执行前 `registry.get` 探测（execute_call 内部）；连续计数，任何已知工具调用清零 | 与「工具失败」区分开，驱动 F2④；parse_error 不算未知（名字合法） |
| Plan 提示注入 | 以 user 消息写入会话（两条固定中文提示，enter/exit 幂等） | 无 system prompt 机制；user 消息两家协议通用、无需 provider 改动 |
| 只读判定 | `Tool.read_only: ClassVar[bool] = True` 默认，写/命令工具显式 False | 一个标记同时服务 Plan Mode 过滤与分批并发分组，语义单一 |
| Token 用量 | `Done.usage: UsageInfo \| None`；Anthropic 取 `message_delta`、OpenAI 加 `stream_options.include_usage` 取最终 chunk | 两家协议各自的官方取法；mock 流中已有 message_delta usage 可复用 |
| TUI 渲染 | 每轮一个 Live（流式期间）；工具调用/结果在流结束后逐条打印（ch04 样式） | 避免 Live 区域与工具面板互相覆盖；视觉与 ch04 连续 |
| 迭代上限 | `MAX_ITERATIONS = 30` 模块常量（构造可覆盖） | N1；测试可注入小值验证上限路径 |
| 异常兜底 | Agent 捕获一切异常 → 回滚本轮 → RoundEnd(ERROR) | 保证事件流永远以 RoundEnd 收尾（N2），后台线程崩溃不会挂起界面 |
