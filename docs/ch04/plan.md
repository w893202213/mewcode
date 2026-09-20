# 工具系统 Plan

## 架构概览

沿用方案 A 的三层分工，在现有架构上扩展而不是重构：

1. **tools/ 包（新增）**：工具系统本体。定义统一 Tool 接口与 ToolResult 结果结构，
   实现六个核心工具、执行超时/异常/截断的统一包装，以及注册中心。
   本包不感知 LLM 协议，不感知 TUI。
2. **provider/ 包（修改）**：职责仍是「协议翻译」。`Provider.stream` 增加 tools 参数，
   各 provider 把 Tool 元信息转成自家 wire 格式塞进请求体；解析流时把工具调用增量
   归一化成新的 `ToolCall` 事件。不执行工具。
3. **agent/ 包（新增）**：`ToolRunner` 承接执行与回灌——按名查工具、确认拦截、
   执行、把工具调用与结果写回会话历史，返回结果供 TUI 展示。
4. **session 层（修改）**：Message 增加归一化的工具调用/结果记录，
   下一轮请求时由 provider 按协议重建 wire 格式（与 thinking_raw 的直通模式不同，
   工具块可由归一化数据重建，保持 session 不感知协议）。
5. **TUI（修改）**：流中收到 ToolCall 时收集；流结束后展示工具名+参数，
   需要确认的工具弹确认提示（y 执行 / n 拒绝），执行后展示结果摘要，回到输入行。

## 核心数据结构

### ToolResult（tools/base.py）

```python
@dataclass
class ToolResult:
    ok: bool       # 执行是否成功
    content: str   # 成功内容或中文错误信息
```

### Tool 抽象接口（tools/base.py）

```python
class Tool(ABC):
    name: ClassVar[str]                  # 工具名（模型可见）
    description: ClassVar[str]           # 工具描述（模型可见）
    input_schema: ClassVar[dict]         # 参数 JSON Schema（模型可见）
    timeout: float = 30.0                # 执行超时上限（秒，N1）
    requires_confirmation: bool = False  # 执行前是否需人工确认（F4）

    @abstractmethod
    def execute(self, args: dict) -> ToolResult: ...
```

超时/异常/截断的统一包装放 `run_tool(tool, args)` 函数（守护线程 + join 超时，
异常转为错误结果，成功内容超 30000 字符截断并附「已截断」），六个工具的实现
只写业务逻辑，不重复处理这些横切关注点。

### ToolRegistry（tools/registry.py）

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None
    def get(self, name: str) -> Tool        # 找不到抛 ToolError
    def list(self) -> list[Tool]

def create_registry() -> ToolRegistry       # 注册六个核心工具
```

wire 格式转换放在 provider 内部（每家各写一个），注册中心只提供 Tool 对象列表。

### ToolCall 事件（provider/base.py）

```python
@dataclass
class ToolCall:
    id: str                          # 调用唯一 ID（回灌 tool_result 时原样引用）
    name: str                        # 工具名
    args: dict                       # 拼接完成的参数
    parse_error: str | None = None   # 参数 JSON 无法解析时的中文说明（不执行，F7）

StreamEvent = ThinkingDelta | TextDelta | ToolCall | Done
```

### 会话记录扩展（session.py）

```python
@dataclass
class ToolUse:
    id: str
    name: str
    args: dict

@dataclass
class ToolResultRecord:
    tool_use_id: str
    content: str
    is_error: bool

# Message 增加字段：
#   tool_uses: list[ToolUse] = []            （assistant 消息携带）
#   tool_results: list[ToolResultRecord] = []（user 消息携带）
# ChatSession 增加方法：
#   add_assistant(..., tool_uses=None)
#   add_tool_result(record)  # 追加一条 user 消息承载工具结果
```

### ToolRunner（agent/runner.py）

```python
class ToolRunner:
    def __init__(self, registry: ToolRegistry, session: ChatSession,
                 confirm: Callable[[str], bool] | None = None) -> None: ...
    def run(self, call: ToolCall) -> ToolResult:
        # parse_error → 错误结果；查名失败 → 错误结果；需确认被拒 → 拒绝结果；
        # 否则 run_tool 执行；最后 add_tool_result 回灌会话；返回结果供 TUI 展示
```

confirm 回调由 TUI 注入（真实交互 y/n），测试时注入 lambda 即可脱网验证。

## 模块设计

### 模块 1：tools/base.py — 工具接口与执行包装
**职责：** Tool 抽象类、ToolResult、ToolError；`run_tool(tool, args)` 统一执行包装
（守护线程 + join 超时 → 超时错误结果；异常 → 错误结果；成功内容按 30000 字符截断）。
**依赖：** 仅标准库。

### 模块 2：tools/files.py — 三个文件工具
- `ReadFileTool`：参数 `{file_path, limit?}`；读文本，limit 限制行数（缺省全读）；
  文件不存在 → 错误结果（含路径）。相对路径以进程 cwd 解析（N3）。
- `WriteFileTool`：参数 `{file_path, content}`；创建或覆盖写入；成功结果含路径与字节数。
- `EditFileTool`：参数 `{file_path, old_text, new_text}`；原文唯一匹配替换（F6）：
  `old_text` 出现 0 次 → 错误（提示原文不存在，请用读文件工具确认）；
  出现 N>1 次 → 错误（提示匹配 N 次及各行号，要求提供更长上下文）；
  唯一匹配 → 替换并写回，成功结果含替换位置行号。
  匹配失败/多次时不写文件。

### 模块 3：tools/search.py — 两个搜索工具
- `FindFilesTool`：参数 `{pattern, path?}`；glob 模式（如 `**/*.py`），
  path 缺省为 cwd；返回排序后的相对路径列表，上限 500 条，超出截断注明。
- `GrepCodeTool`：参数 `{pattern, path?, case_sensitive?}`；正则搜索文本文件内容，
  跳过二进制与隐藏目录；返回 `文件:行号: 内容` 列表，上限 200 行，超出截断注明；
  正则编译失败 → 错误结果（含模式与原因）。

### 模块 4：tools/shell.py — 命令执行工具
- `RunCommandTool`：参数 `{command}`；`requires_confirmation = True`；
  `subprocess.run(shell=True, capture_output=True, text=True)`，
  内部超时略小于 Tool.timeout（超时杀进程）；
  结果内容 = stdout + stderr + 退出码说明；退出码非零 → `ok=False`（F7）。
  工作目录 = 进程 cwd；不设白名单/沙箱（spec 不做的事）。

### 模块 5：tools/registry.py
**职责：** 登记与查找。**对外接口：** register / get / list / create_registry。

### 模块 6：provider/base.py（修改）
**职责：** `StreamEvent` 联合类型增加 `ToolCall`；`Provider.stream` 签名改为
`stream(messages, tools: list[Tool])`。**依赖：** tools.base（仅元信息）。

### 模块 7：provider/anthropic.py（修改）
- 请求体：`"tools": [{"name", "description", "input_schema"}]`
- 流解析：`content_block_start` 的 `tool_use` 块记录 id/name 并开始累积；
  `content_block_delta` 的 `input_json_delta.partial_json` 追加碎片；
  `content_block_stop` 时 `json.loads` 出完整参数 → `ToolCall`（失败 → parse_error）；
  `message_stop` 时照旧产出 Done。
- 回放重建（_build_payload）：assistant 消息 content = thinking 块 + tool_use 块
  `{"type":"tool_use","id","name","input"}` + text 块；
  带 tool_results 的 user 消息 content = `{"type":"tool_result","tool_use_id","content","is_error"}` 列表。

### 模块 8：provider/openai.py（修改）
- 请求体：`"tools": [{"type":"function","function":{"name","description","parameters"}}]`
- 流解析：`choices[0].delta.tool_calls` 按 index 聚合——首个碎片带 id/name，
  `function.arguments` 碎片串接；`finish_reason == "tool_calls"` 时逐条 `json.loads`
  出参数 → `ToolCall`（失败 → parse_error），随后产出 Done。
- 回放重建：assistant 消息 `{role, content: null, tool_calls: [{id, type:"function",
  function:{name, arguments: json.dumps(args)}}]}`；
  工具结果每条一条 `{"role":"tool","tool_call_id","content"}` 消息。

### 模块 9：agent/runner.py
**职责：** ToolRunner（见核心数据结构），执行与回灌。**依赖：** tools、session。

### 模块 10：tui/app.py（修改）
- `_chat_round` 收集 ToolCall（F8：只执行第一个，其余提示忽略）；流结束后：
  显示 `🔧 工具名` + 参数摘要（JSON 截断 200 字符）→ 需要确认则弹 y/n 提示
  （空输入=拒绝）→ `ToolRunner.run` → 结果面板（成功绿/失败红，内容按显示上限截断）。
- 无工具调用时行为与现在完全一致。
- `run()` 装配：`create_registry()` → `ToolRunner` → 把 registry 传入 `_chat_round`，
  provider.stream 调用处带上 `registry.list()`。

### 模块 11：scripts/mock_llm.py（修改）
- 新增两类流：Anthropic 工具调用流（tool_use 块 + 分片 input_json_delta）与
  OpenAI 工具调用流（带 index 的 tool_calls 碎片，finish_reason="tool_calls"）。
- 触发条件：请求 body 含 `"tools"` 且 model 名为 `tool-model` 时返回工具调用流；
  其余行为不变（继续用 mock_requests.log 验证请求体）。

## 模块交互

```
用户提问
  → tui._chat_round: session.add_user(text)
  → provider.stream(session.messages, tools=registry.list())
        ├─ anthropic: 流解析出 ThinkingDelta / TextDelta / ToolCall / Done
        └─ openai:   流解析出 TextDelta / ToolCall / Done
  → Done 后: session.add_assistant(text, thinking, thinking_raw, tool_uses=[第一个 ToolCall])
  → 有 ToolCall 时:
        TUI 展示调用 → （需确认）y/n 提示 → ToolRunner.run(call)
            ├─ registry.get(name) → run_tool(tool, args) → ToolResult
            └─ session.add_tool_result(record)      ← 结果回灌（F5）
        TUI 展示结果 → 本轮结束（无自动循环）
  → 下一轮请求: provider._build_payload 按协议重建 tool_use/tool_result 块
```

异常路径：ProviderError → 现有红色面板；工具失败 → 错误结果正常展示并回灌，
不抛异常、不崩溃（F7）；Ctrl+C 流式中断 → 现有回滚逻辑不变（N6）。

## 文件组织

```
mewcode/
├── agent/
│   ├── __init__.py        — 导出 ToolRunner
│   └── runner.py          — ToolRunner：执行、确认拦截、结果回灌
├── tools/
│   ├── __init__.py        — 导出 Tool/ToolResult/registry，create_registry
│   ├── base.py            — Tool 抽象、ToolResult、ToolError、run_tool 执行包装
│   ├── files.py           — ReadFileTool / WriteFileTool / EditFileTool
│   ├── search.py          — FindFilesTool / GrepCodeTool
│   ├── shell.py           — RunCommandTool
│   └── registry.py        — ToolRegistry
├── provider/
│   ├── base.py            — （改）StreamEvent + ToolCall；stream 签名加 tools
│   ├── anthropic.py       — （改）tools 请求体、tool_use 流解析、回放重建
│   ├── openai.py          — （改）tools 请求体、tool_calls 碎片聚合、回放重建
│   └── __init__.py        — 不变
├── session.py             — （改）ToolUse/ToolResultRecord；Message 扩展；add_tool_result
├── tui/
│   └── app.py             — （改）工具调用收集/展示/确认/结果面板
└── cli.py                 — （改）create_registry → run(provider, config, registry)

tests/
├── test_tools.py          — 六个工具 + run_tool 超时/异常/截断
├── test_registry.py       — 登记/查找/重复注册
├── test_provider_tools.py — 双协议流解析（碎片→ToolCall）+ 请求体 tools 字段 + 回放重建
├── test_session_tools.py  — tool_uses/tool_results 记录与回放
└── test_runner.py         — ToolRunner 全路径（含拒绝、未知工具、parse_error）

scripts/
└── mock_llm.py            — （改）tool-model 返回工具调用流
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具执行位置 | 独立 agent/ 包（方案 A） | provider 保持纯翻译可测；下一章 Agent Loop 只扩展 runner |
| tools 参数形态 | `Provider.stream(messages, tools: list[Tool])`，provider 自行转 wire 格式 | 调用方不感知协议；转换逻辑与协议解析同居一处 |
| 会话回放数据 | 存归一化 ToolUse/ToolResultRecord，provider 在 _build_payload 重建 | session 不感知协议；重建仅需 id/name/args 三个字段 |
| 参数碎片解析失败 | ToolCall.parse_error 携带原因，runner 返回错误结果而不执行 | 满足 F7「失败信息回给模型」；不因单条坏调用崩掉整轮 |
| 超时实现 | 守护线程 + join(tool.timeout)；shell 内部另有 subprocess 超时（略小）杀进程 | 同步工具的统一超时方案；线程不可杀，子进程可由 subprocess 兜底 |
| 命令确认交互 | runner 注入 confirm 回调；TUI 用独立 PromptSession 弹 `[y/n]`，空输入=拒绝 | 安全默认拒绝；回调注入让测试脱网可测 |
| 确认时机 | 流结束（Done）之后弹确认，再执行 | 流式期间 Live 正渲染，结束后交互干净；确认信息基于完整参数 |
| 多工具调用（F8） | 只执行第一个 ToolCall，其余忽略并计数提示；会话只记录第一个 | 贴合「先做单工具」边界，下一章循环化时去掉限制即可 |
| mock 触发 | model 名为 `tool-model` 且请求含 tools 字段时返回工具调用流 | 显式触发不干扰既有文本流用例；mock_requests.log 继续验证请求体 |
| 内容上限 | 回灌内容 30000 字符截断（run_tool 统一保证，满足 N2）；TUI 展示面板为可读性在 30000 上限内另行 2000 字符截断 | 回灌保完整、终端不刷屏 |
| 新增依赖 | 无（pathlib/subprocess/re/fnmatch/threading/json） | 满足 N5 |
