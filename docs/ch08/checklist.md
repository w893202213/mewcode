# MCP 客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 配置文件声明 stdio Server（command/args/env）与 http Server（url/headers）均能解析（验证：`python -m pytest tests/test_mcp_config.py -q` 全绿）
- [ ] `${VAR}` 展开正确，未定义变量报中文配置错误（验证：`tests/test_mcp_config.py` 对应用例）
- [ ] 缺必填字段/双字段/类型非法的 Server 配置报中文 ConfigError（验证：`tests/test_mcp_config.py` 对应用例）
- [ ] 用户级/项目级两层合并：同名覆盖、项目级缺失或字段缺失正常忽略、两份都无 → 空列表不启用（验证：`tests/test_mcp_config.py` 对应用例）
- [ ] RpcClient 按 id 配对（乱序响应）、错误/通知/Server 请求三态、坏 JSON 连接损坏、超时（验证：`tests/test_mcp_protocol.py` 全绿）
- [ ] stdio 传输真实子进程收发；HTTP 传输单 JSON/SSE 响应、Session-Id 回带（验证：`tests/test_mcp_transports.py` 全绿）
- [ ] 握手版本协商、tools/list 游标翻页聚合、content text 提取与非 text 占位、isError 转失败（验证：`tests/test_mcp_session.py` 全绿）
- [ ] McpTool 成功/远端错误/超时/连接失败 → ToolResult 正确；工具名白名单校验（验证：`tests/test_mcp_adapter.py` 全绿）
- [ ] 规则文件允许按任意工具名配置；注册后 validate_tools 报非法名（验证：`tests/test_rules.py` 全绿）

## 集成

- [ ] 单个 Server command 不存在/URL 连不上：启动打印中文警告、不退出，其余 Server 工具与内置六工具照常可用（验证：`tests/test_mcp_manager.py` 对应用例）
- [ ] 重名策略：MCP 工具与内置工具重名跳过并警告；两 Server 同名工具按声明顺序先注册优先（验证：`tests/test_mcp_manager.py` 对应用例）
- [ ] 运行期 Server 进程被杀：其工具调用返回中文错误结果，其他工具不受影响（验证：`tests/test_mcp_manager.py` 对应用例）
- [ ] shutdown 后 stdio 子进程全部终止、线程退出、幂等可重入（验证：`tests/test_mcp_manager.py` 对应用例；pytest 退出后 `ps` 无 mock_mcp_server 残留）
- [ ] MCP 工具走权限闸门：read_only=False 默认模式询问、严格拒绝、放行放行（验证：`tests/test_mcp_manager.py` 对应用例或 TUI 端到端）
- [ ] 规则 `tool: mcp_tool_name, pattern: "*"` 命中后免询问放行（验证：端到端场景或单测）
- [ ] cli 装配链完整：providers → 闸门 → 内置工具 → MCP 发现 → validate_tools → run → finally shutdown（验证：`python -m compileall mewcode/` 通过；无 MCP 配置启动行为与 ch07 完全一致）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -m compileall mewcode/ scripts/`）
- [ ] 全部单元测试通过（验证：`python -m pytest tests/ -q` 退出码 0）
- [ ] 既有回归无损：六个内置工具、Agent Loop、权限五层、TUI、配置解析全部既有测试通过（验证：同上一条全量跑绿）

## 端到端场景（tmux）

- [ ] 场景 1（stdio 全链路）：配置声明 mock Server → tmux 启动 MewCode → 让 Agent 用 MCP 工具（如 add 算 1+2）→ 默认模式弹出确认询问 → 确认后返回正确结果（AC1/AC8）
- [ ] 场景 2（坏 Server 隔离）：配置里加一个 command 不存在的 Server → 启动打印中文警告不退出 → 好 Server 的工具仍可用（AC5）
- [ ] 场景 3（重名）：mock Server 声明一个与内置工具同名的工具 → 启动警告跳过 → 内置工具行为不变（AC7）
- [ ] 场景 4（规则放行）：规则文件写 `mcp 工具名(*) → allow` → 调该工具免询问直接执行（AC9）
- [ ] 场景 5（退出无残留）：退出 MewCode 后 `ps` 检查 mock Server 子进程已终止（AC10）
