# MCP 客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 配置文件声明 stdio Server（command/args/env）与 http Server（url/headers）均能解析（验证：`pytest tests/test_mcp_config.py -q` 全绿 22 例）（AC1、AC2）
- [x] `${VAR}` 展开正确，未定义变量报中文配置错误（验证：`tests/test_mcp_config.py` test_expand_var* 用例；e2e 中 `Bearer ${MCP_TOKEN}` 正常展开）（AC3）
- [x] 缺必填字段/双字段/类型非法的 Server 配置报中文 ConfigError（验证：`tests/test_mcp_config.py` 对应用例）（AC3）
- [x] 用户级/项目级两层合并：同名覆盖、项目级缺失或字段缺失正常忽略、两份都无 → 空列表不启用（验证：`tests/test_mcp_config.py` test_merge_* 用例）（AC4）
- [x] RpcClient 按 id 配对（乱序响应）、错误/通知/Server 请求三态、坏 JSON 连接损坏、超时（验证：`pytest tests/test_mcp_protocol.py -q` 全绿 9 例）
- [x] stdio 传输真实子进程收发；HTTP 传输单 JSON/SSE 响应、Session-Id 回带（验证：`pytest tests/test_mcp_transports.py -q` 全绿 10 例）
- [x] 握手版本协商、tools/list 游标翻页聚合、content text 提取与非 text 占位、isError 转失败（验证：`pytest tests/test_mcp_session.py -q` 全绿 11 例）
- [x] McpTool 成功/远端错误/超时/连接失败 → ToolResult 正确；工具名白名单校验（验证：`pytest tests/test_mcp_adapter.py -q` 全绿 16 例）
- [x] 规则文件允许按任意工具名配置；注册后 validate_tools 报非法名（验证：`pytest tests/test_rules.py -q` 全绿 17 例）（AC9）

## 集成

- [x] 单个 Server command 不存在/URL 连不上：启动打印中文警告、不退出，其余 Server 工具与内置六工具照常可用（验证：`tests/test_mcp_manager.py` test_bad_server_skipped_others_continue；e2e 场景 2 中坏 Server 警告后 add 照常调用）（AC5）
- [x] 重名策略：MCP 工具与内置工具重名跳过并警告；两 Server 同名工具按声明顺序先注册优先（验证：`tests/test_mcp_manager.py` test_conflict_* 用例；e2e 场景 3 中 read_file 冲突警告且内置工具行为不变）（AC7）
- [x] 运行期 Server 进程被杀：其工具调用返回中文错误结果，其他工具不受影响（验证：e2e 运行中 kill mock Server 进程 → 工具失败面板「MCP 工具 'add' 调用失败: MCP 连接已损坏，无法继续调用」→ Agent 第 2 轮照常继续并输出最终回复；另有 `tests/test_mcp_transports.py` test_stdio_eof_raises 单测）（AC6）
- [x] shutdown 后 stdio 子进程全部终止、线程退出、幂等可重入（验证：`tests/test_mcp_manager.py` test_shutdown_kills_subprocesses_and_idempotent；e2e 场景 5 退出后 ps 查残留数为 0）（AC10）
- [x] MCP 工具走权限闸门：read_only=False 默认模式询问、严格拒绝、放行放行（验证：e2e 场景 1/HTTP 场景均弹出「需要权限确认」面板；闸门层序单测覆盖三档兜底）（AC8）
- [x] 规则 `tool: mcp_tool_name, pattern: "*"` 命中后免询问放行（验证：e2e 场景 4 全程 0 次询问面板，工具直接执行并回灌）（AC9）
- [x] cli 装配链完整：providers → 闸门 → 内置工具 → MCP 发现 → validate_tools → run → finally shutdown（验证：`python -m compileall mewcode/ scripts/` 通过；`python -m mewcode --help` 正常；无 MCP 配置启动行为与 ch07 一致——基线测试全绿）
- [x] 真实 Server 联调修复（context7 官方 Streamable HTTP）：HttpTransport 按规范接受 notification 的 202 Accepted 应答（此前误判为连接失败，握手在 notifications/initialized 步失败导致 Server 被跳过）（验证：新增 `tests/test_mcp_transports.py` test_http_202_notification_accepted；真实 context7 发现注册出 resolve-library-id/query-docs 两工具）

## 编译与测试

- [x] 项目编译无错误（验证：`python -m compileall mewcode/ scripts/` 退出码 0）
- [x] 全部单元测试通过（验证：`pytest tests/ -q` 321 passed，退出码 0；较基线 320 新增 1 例 202 用例）
- [x] 既有回归无损：六个内置工具、Agent Loop、权限五层、TUI、配置解析全部既有测试通过（验证：全量 320 = 基线 243 + 本章新增 77，无既有用例失败）

## 端到端场景（tmux）

- [x] 场景 1（stdio 全链路）：配置声明 mock Server → tmux 启动 MewCode 无警告 → Agent 调用 add → 默认模式弹出确认询问 → 按 y 确认 → 工具结果「1 + 2 = 3」回灌 → 第二轮输出「完成！MCP 工具链可用，结果已回灌。」（AC1/AC8）
- [x] 场景 2（坏 Server 隔离）：配置里加 command 不存在的 broken Server → 启动打印「警告: MCP Server 'broken' 启动失败: …已跳过」且不退出 → demo Server 的 add 工具照常调用并回灌（AC5）
- [x] 场景 3（重名）：mock Server 声明与内置同名的 read_file → 启动警告「工具 'read_file' 与已注册工具重名，已跳过」→ 内置 read_file 正常执行（返回「文件不存在」走内置链路，未被覆盖）（AC7）
- [x] 场景 4（规则放行）：规则文件写 `add(*) → allow` → 调 add 全程 0 次「需要权限确认」面板，直接执行并回灌（AC9）
- [x] 场景 5（退出无残留）：退出 MewCode 后 ps 检查 mock_mcp_server 子进程残留数为 0（AC10）
- [x] 场景 6（HTTP 全链路，AC2）：stdio mock 经临时桥接脚本包装成 Streamable HTTP（SSE 形态）→ 配置 `url + headers（${MCP_TOKEN} 展开）` → 启动无警告 → Agent 调用 add → 确认 → 工具结果「1 + 2 = 3」回灌 → 最终回复正常
- [x] 场景 7（真实 context7 HTTP 全链路，AC2/AC8）：用户级 `~/.mewcode/config.yaml` 声明 context7（url + Bearer headers）→ tmux 启动无警告，注册出 resolve-library-id/query-docs → 提问「你现在能调用MCP工具吗？」→ Agent 经 run_command 查配置后自证调用 resolve-library-id（确认面板放行）返回 5 条候选，最终回复「能，而且刚实测通过」→ 追问 requests 文档 → query-docs 两次真实调用（安装方式/GET 用法）回灌真实文档，最终回复含代码示例与坑点提示 → /exit 退出，无残留进程
