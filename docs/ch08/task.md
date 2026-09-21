# MCP 客户端 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/mcp/__init__.py` | 包导出 |
| 新建 | `mewcode/mcp/config.py` | McpServerConfig、expand_var、两层合并加载 |
| 新建 | `mewcode/mcp/protocol.py` | McpServerError、RpcClient（id 配对/三态/超时） |
| 新建 | `mewcode/mcp/transports.py` | Transport 抽象、StdioTransport、HttpTransport（JSON+SSE）、create_transport |
| 新建 | `mewcode/mcp/session.py` | McpSession：握手/列工具/调工具/同步桥接/循环线程 |
| 新建 | `mewcode/mcp/tool_adapter.py` | McpTool(Tool) |
| 新建 | `mewcode/mcp/manager.py` | McpManager：发现注册/重名跳过/统一关闭 |
| 新建 | `scripts/mock_mcp_server.py` | 最小 stdio MCP Server（echo/add/fail 三工具，支持强制分页），测试与 e2e 共用（相对 plan 的调整：不再单列 tests/fixtures 副本，一份两用） |
| 修改 | `mewcode/permissions/rules.py` | 工具名校验放宽 + validate_tools |
| 修改 | `mewcode/cli.py` | 装配 MCP 发现、闸门校验、finally shutdown |
| 新建 | `tests/test_mcp_config.py` | 配置校验、${VAR} 展开、两层合并 |
| 新建 | `tests/test_mcp_protocol.py` | RpcClient id 配对、超时、三态（假 Transport） |
| 新建 | `tests/test_mcp_transports.py` | stdio 真实子进程；http 用 stdlib http.server 起 JSON/SSE 端点 |
| 新建 | `tests/test_mcp_session.py` | 握手版本校验、游标分页、content 提取、isError |
| 新建 | `tests/test_mcp_adapter.py` | McpTool 接口、超时、ToolResult 转换 |
| 新建 | `tests/test_mcp_manager.py` | 多 Server 隔离、重名跳过、shutdown 无残留 |
| 修改 | `tests/test_rules.py` | 任意工具名加载 + validate_tools |

## T1: 配置层（McpServerConfig + ${VAR} 展开 + 两层合并）

**文件：** `mewcode/mcp/config.py`、`mewcode/mcp/__init__.py`（先建骨架）、`tests/test_mcp_config.py`
**依赖：** 无
**步骤：**
1. 建 `mcp/__init__.py` 与 `mcp/config.py`；定义 `McpServerConfig` dataclass（name/transport/command/args/env/url/headers）
2. `expand_var(value)`：`${VAR}` 从 `os.environ` 展开（不存在的变量报 ConfigError，指明变量名）
3. `_parse_server(name, item)`：map 校验；有 command → stdio（command 非空、args 字符串列表、env map 值展开），有 url → http（url 非空、headers map 值展开）；两者皆无/皆有、类型非法 → ConfigError 指明 Server 名与字段
4. `load_mcp_servers(user_path, project_path)`：两份 YAML 的 `mcp_servers` map 按键合并（同名整条目覆盖，保留原位置）；文件缺失/字段缺失/非 map → 忽略；两份都没有 → 空列表；YAML 损坏 → ConfigError
5. 单测：stdio/http 正常解析；缺字段/双字段/类型非法报错；${VAR} 展开与未定义报错；两层合并（同名覆盖/项目级缺失/都缺失/项目级非 map 忽略）

**验证：** `python -m pytest tests/test_mcp_config.py -q` 全绿

## T2: mock MCP Server（测试共用子进程）

**文件：** `scripts/mock_mcp_server.py`
**依赖：** 无
**步骤：**
1. stdio 循环：逐行读 stdin JSON；initialize → 返回 `protocolVersion: 2025-03-26`、`capabilities: {tools: {}}`；`initialized` 通知忽略
2. `tools/list` → 返回 echo（参数 text）/add（参数 a、b）/fail 三工具；环境变量 `MOCK_PAGE_SIZE` 存在时按该值分页返回（nextCursor），供翻页测试
3. `tools/call` → echo 回显文本；add 求和；fail 返回 `isError: true` 与错误文本
4. 未知 method → JSON-RPC method-not-found 错误
5. 保证每条消息单行输出（`json.dumps` 后 flush）

**验证：** 手动管道冒烟：`printf '...initialize...\n' | python scripts/mock_mcp_server.py` 输出合法响应（或在 T4 的子进程测试中验证）

## T3: 协议层（RpcClient id 配对/三态/超时）

**文件：** `mewcode/mcp/protocol.py`、`tests/test_mcp_protocol.py`
**依赖：** T1（同包导入）
**步骤：**
1. `McpServerError`（异常，消息中文，可携带 code/data）
2. `RpcClient(transport)`：`request(method, params, timeout)` id 自增挂 pending；`notify(method, params)` 无 id；`start()` 起读循环；`close()`
3. 读循环：响应（有 id）按 id 配对唤醒；错误（有 id + error）→ 配对的 future 抛 McpServerError；通知（无 id 无 result/error）忽略；Server 请求（无 id 无 result/error，有 method）→ 回 method-not-found 错误
4. 坏 JSON 一行 → 连接标记损坏，所有 pending 报中文「连接已损坏」错误
5. 超时：`request` 到点未配对 → 移除 pending 并抛中文超时错误
6. 单测用假 Transport（asyncio.Queue 驱动 read/write）：配对正确（乱序响应）、错误响应、通知忽略、Server 请求回 -32601、坏 JSON 损坏、超时

**验证：** `python -m pytest tests/test_mcp_protocol.py -q` 全绿

## T4: 传输层（stdio + Streamable HTTP）

**文件：** `mewcode/mcp/transports.py`、`tests/test_mcp_transports.py`
**依赖：** T2（stdio 用 mock 子进程）、T3（接口对齐）
**步骤：**
1. `Transport` 抽象：`connect/read_message/write_message/close` 四协程方法
2. `StdioTransport(config)`：`asyncio.create_subprocess_exec(command, *args)`，env 合并 os.environ ∪ 配置 env；写单行 JSON；按行读 JSON；close → terminate + wait（进程不存在则跳过）
3. `HttpTransport(config)`：httpx.AsyncClient；`write_message` → POST（`Accept: application/json, text/event-stream`，合并配置 headers，带已存 `Mcp-Session-Id`）；响应 `application/json` 直接入接收队列；`text/event-stream` 按 `event: message` + `data:` 行解析逐条入队（data 是 JSON 文本）
4. `create_transport(config)` 按 transport 字段选型
5. 单测：stdio 用 `sys.executable scripts/mock_mcp_server.py` 真实收发 initialize；http 用 stdlib `http.server` 线程端点分别测单 JSON 响应、SSE 响应（含多事件）、Session-Id 回带、非 200 状态转中文错误

**验证：** `python -m pytest tests/test_mcp_transports.py -q` 全绿

## T5: 会话层（握手/翻页/调工具/同步桥接）

**文件：** `mewcode/mcp/session.py`、`tests/test_mcp_session.py`
**依赖：** T3、T4
**步骤：**
1. `ToolSpec` dataclass（name/description/input_schema）
2. `McpSession(config)`：构造时创建专属 daemon 线程跑 `asyncio.new_event_loop()`，所有协程经 `run_coroutine_threadsafe` 投递
3. `start()`（10 秒时限）：connect → `initialize`（`protocolVersion: "2025-03-26"`、`capabilities: {"tools": {}}`、`clientInfo`）→ 校验响应版本在 `{2024-11-05, 2025-03-26, 2025-06-18}` → 发 `initialized` 通知
4. `list_tools()`：`tools/list` 游标翻页聚合为 `list[ToolSpec]`（cursor 缺失即末页；非法工具条目跳过）
5. `call_tool(name, args)`：`tools/call`；content 列表逐项提取 text；非 text 项以「（MCP 返回了 image 类型内容，无法在终端展示）」占位；`isError` 抛中文 McpServerError；空 content → 空串
6. `call_tool_sync(name, args)`：桥接 + 25 秒超时；`close_sync()`：停循环线程并 join（幂等）
7. 单测用假 Transport 驱动 McpSession 核心逻辑（桥接走真实线程）：握手成功/版本不支持失败、翻页聚合、text 提取、非 text 占位、isError、空 content；call_tool_sync 超时中文消息；close_sync 幂等

**验证：** `python -m pytest tests/test_mcp_session.py -q` 全绿

## T6: 适配层（McpTool）

**文件：** `mewcode/mcp/tool_adapter.py`、`tests/test_mcp_adapter.py`
**依赖：** T5
**步骤：**
1. `McpTool(Tool)`：构造入 ToolSpec + session；实例属性 name/description/input_schema；`read_only = False`；`timeout = 30`
2. `input_schema` 缺省 `{"type": "object", "properties": {}}`
3. `execute(args)`：调 `session.call_tool_sync`；`McpServerError`/任何异常 → `ToolResult.failure`（中文）；成功 → `ToolResult.success`
4. `tool_name_valid(name)`：`^[a-zA-Z0-9_-]{1,64}$` 校验（注册前用）
5. 单测：execute 成功/远端错误/超时/连接失败 → ToolResult 正确；名校验边界（含点、空名、超长）

**验证：** `python -m pytest tests/test_mcp_adapter.py -q` 全绿

## T7: 管理层（多 Server 发现注册/关闭）

**文件：** `mewcode/mcp/manager.py`、`mewcode/mcp/__init__.py`（补导出）、`tests/test_mcp_manager.py`
**依赖：** T1、T5、T6
**步骤：**
1. `McpManager(servers)`；`discover_and_register(registry)`：按声明顺序对每 Server 建 McpSession → 并行提交 start+list_tools → 逐工具：名不合法跳过警告 → 重名（含内置六工具）跳过警告 → `registry.register(McpTool)`；**任何阶段失败打印中文警告跳过该 Server，方法不抛异常**；返回注册工具总数
2. 警告格式：「MCP Server 'xxx' 启动失败: <中文原因>，已跳过」（不回显 headers/env）
3. `shutdown()`：逐个 close_sync，单个异常不中断其余，幂等可重入
4. 单测：成功 Server 工具注册；一个 Server command 不存在 → 警告且其余照常；重名（与内置、与前一 Server）跳过；shutdown 后子进程无残留（poll 非 None）；shutdown 幂等

**验证：** `python -m pytest tests/test_mcp_manager.py -q` 全绿

## T8: 规则校验放宽（任意工具名 + 注册后统一校验）

**文件：** `mewcode/permissions/rules.py`、`tests/test_rules.py`
**依赖：** 无（与 T3–T7 可并行）
**步骤：**
1. `_parse_rule_file`：tool 校验改为「非空字符串」（不再查 `TOOL_NAMES`）
2. `RuleSet.validate_tools(known)`：文件来源规则的工具名不在 known 集合 → ConfigError 列出非法名；会话规则不参与
3. 既有 test_rules.py 中「未知工具名报错」用例改为「任意工具名可加载」+ 新增 validate_tools 用例（合法/非法/会话规则豁免）

**验证：** `python -m pytest tests/test_rules.py -q` 全绿

## T9: CLI 装配

**文件：** `mewcode/cli.py`
**依赖：** T7、T8
**步骤：**
1. `_build_gate` 保持不变（规则已放宽）
2. `main()`：`create_registry()` 后调 `load_mcp_servers(args.config, Path.cwd()/".mewcode"/"config.yaml")`（ConfigError → 现有退出路径）；建 `McpManager` 并 `discover_and_register(registry)`
3. `gate.validate_tools({t.name for t in registry.list()})`（ConfigError → 退出）
4. `run(...)` 包 try/finally：`finally: manager.shutdown()`；无 MCP 配置时 manager 为空对象，shutdown 为无害空操作

**验证：** `python -m mewcode --help` 正常；`python -m compileall mewcode/` 通过

## T10: 全量回归

**文件：** 无（跑测试）
**依赖：** T1–T9
**步骤：**
1. `python -m pytest tests/ -q` 全绿（含既有 test_rules 适配后的全部测试）
2. 检查无残留子进程、无僵尸线程（pytest 正常退出）

**验证：** 全部测试通过，退出码 0

## T11: e2e 专用启动配置与 tmux 端到端验收

**文件：** `scripts/mock_mcp_server.py`（已有，若 e2e 需要调整在此改）
**依赖：** T10
**步骤：**
1. 准备 e2e 配置：用户级 config 加 `mcp_servers: {demo: {command: python, args: [scripts/mock_mcp_server.py]}}`（临时文件，不进 git 的正式配置）
2. tmux 启动 MewCode，观察启动警告区无错误
3. 输入一段请求（如「用 add 工具算 1+2」），观察：Agent 调 MCP 工具 → 权限询问（默认模式）→ 确认 → 返回正确结果
4. 逐项对照 checklist.md 验收

**验证：** checklist.md 全部勾选

## 执行顺序

```
T1 ──→ T3 ──→ T4 ──→ T5 ──→ T6 ──→ T7 ──→ T9 ──→ T10 ──→ T11
 └─→ T2 ─┘                       
T8 ──────────────────────────────→ T9
```

（T2 只依赖 mock 自身；T8 只依赖 T1 的包结构，可与 T3–T7 并行）
