# MCP 客户端 Plan

## 架构概览

新增 `mewcode/mcp/` 包，六个模块分层：

```
配置层  mcp/config.py       McpServerConfig + ${VAR} 展开 + 两层合并加载
协议层  mcp/protocol.py     JSON-RPC 2.0：消息类型、请求按 id 异步配对
传输层  mcp/transports.py   Transport 抽象 + StdioTransport + HttpTransport
会话层  mcp/session.py      McpSession：握手 → 列工具 → 调工具（含同步桥接）
适配层  mcp/tool_adapter.py McpTool(Tool)：远端工具包装成内置工具接口
管理层  mcp/manager.py      McpManager：多 Server 发现、注册、关闭
```

核心思路：

1. **每 Server 一个后台线程 + 独立 asyncio 事件循环**。MCP 协议天然异步（stdio 管道读、HTTP 请求），而 `Tool.execute` 是同步接口；每个 Server 的连接（含 stdio 子进程）跑在专属线程的事件循环里，同步侧用 `run_coroutine_threadsafe(...).result(timeout)` 桥接。故障隔离：一个 Server 的循环卡死只影响它自己（F6/N1）。
2. **发现时并行**：每个 Server 的「连接 + 握手 + 列工具」提交到各自循环并行执行，单个超时 10 秒、互不拖慢（N2）。
3. **注册即无缝**：发现到的工具包装成 `McpTool` 直接 `registry.register()`，Agent 循环、provider 转换、权限闸门全走现有链路，无需感知 MCP（F5）。
4. **失败口径分级**：配置格式错误 → `ConfigError` 整体退出；连接/握手失败 → 打印中文警告跳过该 Server（F6）。

## 核心数据结构

### `McpServerConfig`（mcp/config.py）

一份 Server 配置，对应两层合并后 `mcp_servers` 的一项：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | Server 名（map 的 key） |
| `transport` | str | `"stdio"` 或 `"http"`（由字段推断：有 command → stdio，有 url → http，两者皆无/皆有 → ConfigError） |
| `command` | str \| None | stdio：可执行命令（必填） |
| `args` | list[str] | stdio：参数（缺省空列表） |
| `env` | dict[str, str] | stdio：额外环境变量，值已做 `${VAR}` 展开 |
| `url` | str \| None | http：端点（必填） |
| `headers` | dict[str, str] | http：额外请求头，值已做 `${VAR}` 展开 |

### `ToolSpec`（mcp/session.py）

`tools/list` 返回的单个工具定义：

- `name: str`、`description: str`、`input_schema: dict`

### `McpServerError`（mcp/protocol.py，异常）

MCP 层错误基类，消息一律中文：远端返回的 JSON-RPC error（`code/message/data`）、协议解析失败、调用超时、连接失败均以它向上抛。

### `RpcClient`（mcp/protocol.py）

传输无关的 JSON-RPC 客户端，每个连接一个实例：

- `request(method, params, timeout) -> result`：发请求（id 自增），挂到 pending 字典，等待同 id 响应；响应/错误到达时配对唤醒；超时移除并抛 `McpServerError`
- `notify(method, params)`：发通知（无 id），不等响应
- `start() / close()`：启动读循环 / 停止
- 读循环三态处理：**响应**（有 id）→ 配对；**通知**（无 id）→ 忽略；**Server 请求**（无 id 无 result/error）→ 回 method-not-found 错误（F3）

### `Transport`（mcp/transports.py，抽象接口）

```python
class Transport(ABC):
    async def connect(self) -> None          # 建立连接（启动子进程/建 HTTP 客户端）
    async def read_message(self) -> dict     # 读入一条 JSON 消息（RpcClient 读循环消费）
    async def write_message(self, msg: dict) -> None
    async def close(self) -> None            # 终止子进程/关闭 HTTP 客户端
```

### `McpSession`（mcp/session.py）

一个 Server 的完整会话（Transport + RpcClient 之上）：

- `async start()`：connect → `initialize`（协议版本 `2025-03-26`、能力 `{"tools": {}}`）→ 校验 Server 返回的版本在支持集 → 发 `initialized` 通知
- `async list_tools() -> list[ToolSpec]`：`tools/list` 游标翻页聚合
- `async call_tool(name, args) -> str`：`tools/call`；提取 content 列表 text 文本，非 text 以占位说明呈现；`isError` → 抛 `McpServerError`
- `call_tool_sync(name, args) -> str`：同步桥接（`run_coroutine_threadsafe` + 25 秒超时）
- `close() / close_sync()`：停 RpcClient、关 Transport、停事件循环线程

### `McpTool`（mcp/tool_adapter.py，实现 Tool 接口）

每个远端工具一个实例：`name/description/input_schema` 取自 `ToolSpec`；`read_only = False`；`timeout = 30`（默认值）；`execute(args)` 调 `session.call_tool_sync`，异常转 `ToolResult.failure`。

### `McpManager`（mcp/manager.py）

- `__init__(servers: list[McpServerConfig])`
- `discover_and_register(registry: ToolRegistry) -> int`：按配置声明顺序并行发现；每个 Server 独立线程循环；工具逐条包装注册，**重名（含内置六工具）跳过并警告**；返回成功注册的工具总数
- `shutdown()`：逐个关闭会话、终止子进程、停线程（幂等，可重入）
- `sessions()`：存活会话列表（测试用）

## 模块设计

### 模块 A：`mcp/config.py` — 配置层

**职责：** 从用户级 + 项目级两个 YAML 读取 `mcp_servers`，校验、`${VAR}` 展开、两层合并。

**对外接口：**
- `expand_var(value: str) -> str`：`${VAR}` 从 `os.environ` 展开；未定义 → `ConfigError`（fail-closed）
- `load_mcp_servers(user_path, project_path) -> list[McpServerConfig]`：两层合并，声明顺序 = 用户级先、项目级后（同名覆盖时保留原位置）

**合并语义：** map 按键合并，**同名 Server 整条目覆盖**（不做字段级深合并——可预测、实现简单）。项目级文件缺失或字段缺失 → 忽略，返回用户级内容；两份都没有 → 空列表（MCP 功能不启用，完全兼容现状）。

**校验口径：** 每条配置必须且只能有 `command` 或 `url` 之一；stdio 的 command 非空字符串、args 是字符串列表、env 是 map 且值展开后为字符串；http 的 url 非空、headers 是 map。任何非法 → `ConfigError`，消息指明 Server 名与缺失字段。

**依赖：** PyYAML、`mewcode.config.ConfigError`。

### 模块 B：`mcp/protocol.py` — JSON-RPC 层

**职责：** 消息收发、id 配对、超时、三态处理。不感知任何具体传输。

**对外接口：** `RpcClient`（见核心数据结构），构造时注入 `Transport`。

**关键行为：**
- 请求 `{"jsonrpc": "2.0", "id": N, "method": ..., "params": ...}`，id 自增
- 响应解析：`{"result": ...}` 成功；`{"error": {code, message}}` → `McpServerError`
- 读循环一条消息坏 JSON → 抛 `McpServerError`，该连接后续调用全部报「连接已损坏」（fail-closed，不做半修复）
- 写互斥：并发 `request` 共享同一 Transport，写侧加 asyncio 锁，读侧单循环

**依赖：** 无（标准库 asyncio/json）。

### 模块 C：`mcp/transports.py` — 传输层

**职责：** 两个 Transport 实现 + 工厂。

- `StdioTransport`：`asyncio.create_subprocess_exec(command, *args)`，`env = os.environ ∪ 配置 env`；消息换行分隔 JSON（MCP stdio 规范：单行、无内嵌换行）；`close()` terminate + wait
- `HttpTransport`：httpx.AsyncClient；每次 `write_message` 发 POST（`Accept: application/json, text/event-stream`，合并配置 headers）；响应两种形态都支持：单 JSON 体直接入队，`text/event-stream` 按 `event: message\ndata: {json}` 逐事件解析入队；服务端返回 `Mcp-Session-Id` 时存下并随后续请求回带
- `create_transport(config: McpServerConfig) -> Transport`：按 transport 字段选型

**依赖：** httpx、asyncio。

### 模块 D：`mcp/session.py` — 会话层

**职责：** MCP 三步流程（握手 → 列工具 → 调工具）+ 同步桥接 + 事件循环线程生命周期。

**关键行为：**
- `start()` 全程限时 10 秒（N2 的单个 Server 时限），超时抛 `McpServerError`
- 版本协商：Server 返回的 `protocolVersion` 不在 `{2024-11-05, 2025-03-26, 2025-06-18}` → 按握手失败处理
- `call_tool` 结果提取：content 是列表，逐项取 `{"type": "text", "text": ...}`；image/resource 等非 text 项以「（MCP 返回了 image 类型内容，无法在终端展示）」占位；content 为空 → 空串
- 事件循环线程：`McpSession` 构造时创建专属线程跑 `asyncio.new_event_loop()`，所有协程提交其上；daemon 线程，`close_sync()` 提交停止任务并 join（防残留子进程）

**依赖：** 模块 B/C、`McpServerError`。

### 模块 E：`mcp/tool_adapter.py` — 适配层

**职责：** 把 `ToolSpec` + `McpSession` 包装成 `Tool` 接口。

**关键行为：**
- 注册前校验工具名符合 `^[a-zA-Z0-9_-]{1,64}$`（MCP 规范与 Anthropic 工具名规则的并集），不合法 → 跳过并警告（防止 provider 层请求整体失败）
- `input_schema` 缺省 `{"type": "object", "properties": {}}`，无 `additionalProperties: false`（参数原样转发，校验交给远端）
- `execute` 返回 `ToolResult`；`call_tool_sync` 的 25 秒超时先于 `run_tool` 的 30 秒触发，让中文「MCP 调用超时」消息先回报，`run_tool` 兜底

**依赖：** `mewcode.tools.base.Tool/ToolResult`、模块 D。

### 模块 F：`mcp/manager.py` — 管理层

**职责：** 多 Server 生命周期编排、发现注册、统一关闭。

**关键行为：**
- `discover_and_register`：按配置顺序，每 Server 建 `McpSession`（各自线程循环）→ 并行 `start()` → 各自 `list_tools()` → 逐条包装注册；**整个方法不抛异常**——单个 Server 任何阶段失败（进程起不来、握手超时、tools/list 出错、工具名不合法）打印中文警告并跳过，不影响其余（F6）
- 重名判定：内置六工具优先，MCP 工具之间按声明顺序先注册优先；重名跳过 + 警告（F7）
- 警告格式：「MCP Server 'xxx' 启动失败: <中文原因>，已跳过」；不回显 headers/env 值（N4）
- `shutdown()`：逐个 `close_sync()`，单个关闭异常不中断其余

**依赖：** 模块 A/D/E、`ToolRegistry`。

### 模块 G（修改）：`permissions/rules.py` — 规则校验放宽

- `_parse_rule_file`：tool 字段改为「非空字符串即合法」（不再查 `TOOL_NAMES`）
- 新增 `RuleSet.validate_tools(known: set[str])`：文件来源规则的工具名不在已知集合 → `ConfigError`，列出非法名与文件位置；会话规则（内存）不参与
- 匹配语义不变：MCP 工具规则匹配值固定空串，因此模式写 `*` 表示「该工具的全部调用」；「本会话允许」的 `exact_rule` 自然覆盖该工具后续全部调用（F8）

### 模块 H（修改）：`mewcode/cli.py` — 装配

装配顺序：providers → 闸门（规则加载，不再验工具名）→ 六个内置工具 → `load_mcp_servers`（ConfigError 退出）→ `McpManager.discover_and_register`（内部警告不抛）→ `gate.validate_tools(registry 全部工具名)`（ConfigError 退出）→ `run(...)`，`finally: manager.shutdown()`。

**依赖：** 模块 A/F、`PermissionGate`。

## 模块交互

**启动链路**（`cli.main`）：

```
load_config(providers)
  → _build_gate：三规则文件加载（tool 名只查格式，不查存在性）
  → create_registry()：六个内置工具
  → load_mcp_servers(user, project)：合并校验（非法 → ConfigError 退出）
  → manager.discover_and_register(registry)
        对每个 Server（声明顺序）：
          McpSession(专属线程事件循环) ── start()：connect → initialize → initialized
                                         ── list_tools()：游标翻页聚合
                                         ── 逐 ToolSpec 校验名 → McpTool → registry.register()
        任何一步失败：中文警告 → 跳过该 Server
  → gate.validate_tools(registry 全部工具名)（非法 → ConfigError 退出）
  → run(provider, config, registry, ...)
  → finally: manager.shutdown()：终止子进程、关 HTTP、停线程
```

**工具调用链路**（运行期，与内置工具完全同路）：

```
Agent 调 MCP 工具
  → 权限闸门 gate.check：黑名单/沙箱按名跳过 → 规则层（* 可命中）→ 模式兜底
     （read_only=False：默认询问/严格拒绝/放行放行）
  → executor.execute_call → registry.get → run_tool(30s)
  → McpTool.execute（Agent 事件循环线程内）
  → session.call_tool_sync：run_coroutine_threadsafe 投递到 Server 专属循环
  → tools/call → id 配对 → 响应解析 → text 提取
  → ToolResult 回灌，循环继续
```

**关闭链路**：`run()` 返回 → `manager.shutdown()` → 逐 Server `close_sync()` → 子进程 terminate + wait、httpx close、事件循环停止、线程 join。

## 文件组织

```
mewcode/
├── mcp/
│   ├── __init__.py        — 导出 McpManager、McpServerConfig、load_mcp_servers、McpServerError
│   ├── config.py          — McpServerConfig、expand_var、load_mcp_servers（两层合并）
│   ├── protocol.py        — RpcClient、McpServerError、JSON-RPC 三态处理
│   ├── transports.py      — Transport 抽象、StdioTransport、HttpTransport、create_transport
│   ├── session.py         — McpSession（握手/列工具/调工具/同步桥接/循环线程）
│   ├── tool_adapter.py    — McpTool(Tool)
│   └── manager.py         — McpManager（发现注册/关闭）
├── cli.py                 — 修改：装配 MCP + 闸门校验 + finally shutdown
└── permissions/rules.py   — 修改：工具名校验放宽 + validate_tools

tests/
├── fixtures/mock_mcp_server.py   — 最小 stdio MCP Server（echo/add 两工具，测试与 e2e 共用）
├── test_mcp_config.py            — 配置校验、${VAR} 展开、两层合并
├── test_mcp_protocol.py          — RpcClient id 配对、超时、三态（假 Transport）
├── test_mcp_transports.py        — stdio 真实子进程收发；http 用 stdlib http.server 起 SSE/JSON 端点
├── test_mcp_session.py           — 握手版本校验、游标分页、content 提取、isError
├── test_mcp_adapter.py           — McpTool 接口、超时消息、ToolResult 转换
├── test_mcp_manager.py           — 多 Server 隔离、重名跳过、shutdown 无残留
└── test_rules.py                 — 补充：任意工具名加载 + validate_tools

scripts/mock_mcp_server.py        — e2e（tmux 验收）用 echo 服务器
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 异步桥接 | 每 Server 一个 daemon 线程跑独立事件循环，`run_coroutine_threadsafe + result(timeout)` | 标准库原生、无新依赖；故障隔离到单 Server；httpx 异步客户端绑定单循环 |
| stdio 消息格式 | 换行分隔 JSON | MCP stdio 规范：消息单行、无内嵌换行 |
| HTTP 响应形态 | 单 JSON 与 SSE 都接受，`Mcp-Session-Id` 回带 | Streamable HTTP 规范要求客户端两者兼容 |
| Server 发来的请求 | 回 method-not-found（-32601） | 明确拒绝未知能力，不静默丢弃 |
| 传输类型推断 | 有 command → stdio、有 url → http，否则 ConfigError | 配置简洁无冗余 type 字段，fail-closed |
| 两层合并语义 | 同名整条目覆盖，非字段级 | 可预测；「改字段重写整个条目」符合常见心智 |
| 调用超时 | 桥接 25s < run_tool 30s | 中文超时消息先回报，run_tool 兜底杀线程 |
| 发现时限 | 每 Server 10s，并行 | 单个坏 Server 不拖慢启动（N2） |
| 工具名白名单校验 | 注册前 `^[a-zA-Z0-9_-]{1,64}$`，不合法跳过警告 | MCP 规范要求；防 provider 层请求整体失败 |
| 规则工具名校验 | 加载时只查格式，注册完成后 `validate_tools` 统一查存在性 | MCP 工具名在规则加载时尚未注册（F8） |
| MCP 工具规则匹配 | 匹配值固定空串，`*` = 该工具全部调用 | MCP 工具无规范主参数；「本会话允许」自然覆盖后续调用 |
