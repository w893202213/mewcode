# MewCode 对话 TUI（第一步）Spec

## 背景

MewCode 是一个终端 AI 编程助手（类似 Claude Code），用 Python 从零开发，当前项目为空。本步是第一步：实现「纯对话」——用户在终端启动 MewCode 后进入交互式对话界面，向大模型提问，回复流式逐字显示，支持多轮对话记忆。同时打好 Provider 抽象基础，为后续扩展新后端留出接口。

## 目标

- 终端内可用的交互式对话界面（prompt_toolkit 输入 + Rich 渲染）
- 支持 Anthropic Claude 与 OpenAI 两种 API 后端，YAML 配置切换
- SSE 流式输出、逐字渲染，Claude extended thinking 实时展示
- 多轮对话上下文记忆（仅会话内）
- Provider 层统一接口，新增后端不改动 TUI 与配置层

## 功能需求

### F1: 配置加载
- 从 YAML 配置文件读取多份供应商配置，每份包含 `name` / `protocol` / `model` / `base_url` / `api_key` / `thinking` 六个字段（`thinking` 可选，缺省 false）
- 启动时用 `--profile 名字` 选择用哪份配置，缺省用第一份
- 配置缺失、字段不全或格式错误时，打印明确错误信息并以非零退出码退出

### F2: 交互式对话界面
- 启动后进入全屏交互界面，显示欢迎语与当前所用配置名
- 用户输入问题回车发送，回复以 Markdown 渲染显示（标题/列表/代码块语法高亮）
- 回复**流式逐字显示**，不是等全部生成完才返回

### F3: 流式调用与 Provider 抽象
- 通过统一 Provider 接口调用 LLM，SSE 流式接收
- `protocol: anthropic` → 请求 `/v1/messages` 流式端点；`protocol: openai` → 请求 `/v1/chat/completions` 流式端点
- `base_url`、`api_key`、`model` 均取自所选配置

### F4: Claude extended thinking
- `thinking: true` 时启用 extended thinking（思考预算 token 数使用代码内置默认值）
- 流中的思考内容**实时展示**，视觉上与正式回复区分（灰色/缩进等）
- `thinking: false` 或不填时不启用

### F5: OpenAI 协议 thinking 透传
- `thinking: true` 时向 OpenAI 协议请求体透传 `thinking` 字段，值为配置中的布尔值，具体行为由服务端（含 OpenAI 兼容服务）决定

### F6: 多轮对话
- 每轮用户输入与 AI 回复都追加进上下文，AI 能记住之前说过的话
- 上下文仅存于当前进程内存，退出即丢弃

### F7: 内置命令
- `/exit` 退出程序
- `/clear` 清空对话上下文重新开始
- `/help` 显示可用命令
- 输入历史可用上下键回溯

### F8: 错误处理
- API 调用失败（网络错误、鉴权失败、限流、超时等）在界面中显示错误信息，程序不崩溃，可继续对话

## 非功能需求

- **N1 语言与依赖**：Python 3.10+；核心依赖 prompt_toolkit、Rich、httpx、PyYAML，不引入重量级框架
- **N2 性能**：本地无额外瓶颈，首 token 延迟与所选 API 相当；流式渲染节流刷新不卡顿。生成回复期间输入挂起属预期行为（打断功能不在本步）
- **N3 可扩展性**：新增协议后端只需实现 Provider 接口并注册，不改动 TUI 与配置加载代码
- **N4 安全**：`api_key` 不在界面、日志、报错信息中明文回显
- **N5 代码质量**：中文注释，模块职责清晰，核心逻辑可脱离 TUI 独立测试

## 不做的事

- ❌ tool use / function calling
- ❌ 文件操作与代码编辑（不读写用户文件）
- ❌ 会话持久化与多会话管理
- ❌ 上下文窗口管理 / 自动截断（超出模型限制时由 API 报错提示）
- ❌ 生成中途打断（流式输出中按 Esc 停止）
- ❌ MCP、插件系统
- ❌ OpenAI 协议下 thinking 之外的任何推理参数映射

## 验收标准

- **AC1 协议切换**：配置含 Claude 与 OpenAI 两份配置，`--profile` 分别选择后，各自按对应协议发起请求并正常回复（验证：分别启动，观察请求成功且回复正确）
- **AC2 TUI 可用**：启动后进入全屏界面，可输入问题、回车发送（验证：tmux 中运行，肉眼观察交互）
- **AC3 流式输出**：回复逐字渐进出现，而非等待后一次性输出（验证：观察输出过程，长回复可见渐进增长）
- **AC4 Markdown 渲染**：代码块语法高亮、标题/列表排版正确
- **AC5 思考展示**：Claude 配置 `thinking: true` 时，思考内容实时出现且样式与正式回复明显区分；`thinking: false` 时不出现思考区
- **AC6 多轮记忆**：第一轮让它「记住我的名字叫 X」，第二轮问「我叫什么」，能正确答出 X
- **AC7 内置命令**：`/exit` 正常退出；`/clear` 后上下文清空（再问「我叫什么」答不出）；`/help` 列出全部命令
- **AC8 错误不崩溃**：使用错误的 `api_key` 发起对话，界面显示错误信息，程序不退出，可继续输入
- **AC9 缺省配置**：不带 `--profile` 启动时使用第一份配置
- **AC10 配置错误退出**：配置文件缺失或格式错误时，打印明确错误信息并以非零退出码退出
- **AC11 thinking 透传**：OpenAI 协议下 `thinking: true` 时，请求体中包含 `thinking` 字段（验证：指向本地 mock 服务检查收到的请求体）
