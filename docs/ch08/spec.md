# MCP 客户端 Spec

## 背景

ch01–ch07 之后，MewCode 能对话、有六个内置工具、跑多轮 Agent Loop、有五层权限防御，但工具集是**封闭的**：用户无法接入外部工具生态。MCP（Model Context Protocol）是业界标准的工具接入协议，Claude Code 等主流助手都支持在配置文件里声明 MCP Server 列表来扩展工具。本章实现一个 MCP 客户端：启动时从配置发现 Server 列表，通过 MCP 协议完成初始化握手、发现工具，再用适配层包装成 MewCode 已有的 Tool 接口注册进工具中心，Agent 使用时与内置工具完全一致。

## 目标

- 外部 MCP Server 的工具无缝接入工具中心，Agent 使用时无感
- 支持两种传输：本地子进程走 stdio 管道，远程走 Streamable HTTP
- 多 Server 隔离：单个 Server 挂了不影响其他 Server 与内置工具
- 声明式配置：用户级/项目级两层 YAML 合并，后层覆盖前层

## 功能需求

### F1: 配置文件声明 Server 列表

- YAML 顶层 `mcp_servers` 是一个 map，key 是 Server 名字，value 是传输配置
- stdio 类型：`command`（必填）、`args`（可选，字符串列表）、`env`（可选，值支持 `${VAR}` 展开）
- HTTP 类型：`url`（必填）、`headers`（可选，值支持 `${VAR}` 展开）
- 校验失败抛 `ConfigError`，消息中文、指明哪个 Server 缺什么字段（fail-closed，与现有配置口径一致）

### F2: 两层配置合并

- 用户级：`~/.mewcode/config.yaml`（即 `--config` 指向的文件）
- 项目级：启动目录下的 `.mewcode/config.yaml`
- 两层 `mcp_servers` map 按键合并，后层覆盖前层（项目级盖用户级）
- 项目级文件缺失或无 `mcp_servers` 字段时正常忽略

### F3: JSON-RPC 2.0 消息协议

- 消息带 `"jsonrpc": "2.0"`；请求带自增 id；响应/错误按 id 配对
- 通知（无 id）只发不回；收到 Server 发来的请求（非响应）回 JSON-RPC method-not-found 错误（MCP 采样等能力不在范围）
- 响应、错误、通知三态区分；协议级错误（解析失败、超时）转中文错误

### F4: 会话三步流程

- **初始化握手**：`initialize` 请求（声明客户端能力与支持的协议版本）→ 收到响应后发 `initialized` 通知
- **列出工具**：`tools/list`，用游标（cursor）遍历分页，聚合完整列表
- **调用工具**：`tools/call`，参数为任意 JSON；结果提取 `content` 列表中的 text 文本，非 text 内容（image/resource 等）以占位说明呈现；`isError: true` 标记为失败结果

### F5: 适配层包装

- 每个远端工具包装成 Tool 接口实现：`name`/`description`/`input_schema` 取自 `tools/list` 结果
- `read_only = False`（外部工具副作用未知）、`timeout` 沿用默认 30 秒
- `execute` 把参数转发给远端，返回 `ToolResult`；远端错误/超时/连接失败转中文错误结果

### F6: 多 Server 生命周期与隔离

- 每个 Server 一个独立连接；启动时连接 + 握手 + 发现
- 单个 Server 启动失败（子进程起不来、握手失败、`tools/list` 出错）：打印中文警告、跳过该 Server，其余照常启动
- 运行期单个工具调用失败只影响该次调用（错误结果回灌，循环继续），不影响其他工具
- 会话结束（TUI 退出）统一关闭全部连接：终止 stdio 子进程、关闭 HTTP 会话

### F7: 重名策略

- 内置六工具优先：MCP 工具与内置工具重名时跳过该 MCP 工具并警告
- 多个 Server 提供同名工具时，按配置声明顺序先注册优先，后到的跳过并警告

### F8: 权限闸门接入

- MCP 工具走权限闸门：黑名单/沙箱按名只盯内置工具，对 MCP 工具不适用
- 规则文件允许按 MCP 工具名配置规则；无效工具名的校验在完成工具注册后统一执行（先装配后校验，fail-closed 保留）
- 模式兜底按 `read_only=False` 裁决：默认模式询问、严格模式拒绝、放行模式放行

## 非功能需求

### N1: 异步桥接

MCP 客户端基于异步 IO（stdio 管道读写、HTTP 请求），而 `Tool.execute` 是同步接口。桥接必须不阻塞 TUI：调用在后台执行，`run_tool` 的 30 秒超时机制兜底，超时后该次调用返回错误结果，不影响连接复用与后续调用。

### N2: 启动发现并行、互不阻塞

多个 Server 的启动发现相互独立、可并行进行；一个 Server 卡住（如握手长时间无响应）不拖慢其他 Server 的发现，也不拖慢 TUI 启动（发现阶段有整体时限）。

### N3: 错误消息一律中文、可读

与现有口径一致（「未知工具: 'xxx'，已注册工具: …」风格）。

### N4: 不引入新依赖，敏感信息不回显

只用标准库 asyncio + 现有 httpx/PyYAML（Python ≥ 3.10）。警告与错误消息不回显 headers（可能含 Authorization）与 env 的完整值。

## 不做的事

- **MCP 资源、提示词、采样等非工具能力**——只做工具调用链路
- **Server 健康检查与自动重连**——连接只在启动时建立，挂了不复活
- **运行期动态增删 Server**（如 `/mcp` 命令）——Server 列表启动时定型
- **MCP 工具的输入 Schema 本地校验**——参数原样转发，校验交给远端（与内置工具口径一致）
- **权限黑名单/沙箱扩展到 MCP 工具**——黑名单正则与路径沙箱只盯内置工具
- **结构化内容的真实传输**——`tools/call` 结果只提取 text，image/resource 等以占位说明呈现
- **stdio 之外的本地传输、HTTP 之外的远程传输**——只支持 stdio 与 Streamable HTTP

## 验收标准

- **AC1**：配置文件声明一个 stdio Server，启动 MewCode 后其工具出现在可用工具中，Agent 能调用并得到正确结果（端到端验证）
- **AC2**：配置文件声明一个 HTTP Server，工具同样可被发现、可调用
- **AC3**：`mcp_servers` 中某 Server 缺必填字段或引用未定义的 `${VAR}`，启动报中文配置错误并退出（fail-closed）
- **AC4**：项目级配置的 `mcp_servers` 与用户级按名合并，同名 Server 项目级覆盖用户级（改一个可观测字段验证生效）
- **AC5**：某 Server 的 command 不存在或 URL 连不上，启动打印中文警告、不退出，其余 Server 工具与内置工具照常可用
- **AC6**：运行期某 Server 进程被杀，其工具调用返回中文错误结果，Agent 循环继续，其他工具不受影响
- **AC7**：MCP 工具与内置工具重名时跳过并警告，内置工具正常可用
- **AC8**：默认模式下调用 MCP 工具触发人工确认询问，确认后执行；拒绝则回灌拒绝结果、循环继续
- **AC9**：规则文件可按 MCP 工具名写 allow 规则，命中后该工具调用免询问直接放行
- **AC10**：退出 TUI 后 stdio 子进程全部终止，无残留进程
- **AC11**：现有回归全部通过（六个内置工具、Agent Loop、权限五层、TUI）
