# 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 六个工具（read_file / write_file / edit_file / run_command / find_files / grep_code）均可通过统一 Tool 接口执行，直接调用各返回结构化结果（验证：`python -m pytest tests/test_tools.py tests/test_registry.py` 全绿）
- [ ] `create_registry()` 返回含六个工具的注册中心，按名查找成功、未知名报中文错误（验证：`python -m pytest tests/test_registry.py`）
- [ ] ToolRunner 全路径：未知工具、参数解析失败、确认拒绝、执行成功、执行失败五种结果均正确且回灌会话（验证：`python -m pytest tests/test_runner.py`）
- [ ] 会话层记录工具调用与结果（验证：`python -m pytest tests/test_session_tools.py`）

## 集成

- [ ] 两家协议的流式工具调用碎片都能拼接成完整 ToolCall（Anthropic input_json_delta / OpenAI 带索引 arguments 碎片；含坏 JSON → parse_error）（验证：`python -m pytest tests/test_provider_tools.py`）
- [ ] 请求体携带 tools 字段且格式正确——mock 收到的请求体含六个工具的名称、描述与参数 Schema（验证：mock_requests.log 检查 / provider 单测）
- [ ] 工具轮次后下一轮请求体包含工具调用与结果（Anthropic：assistant 含 tool_use 块 + user 含 tool_result 块；OpenAI：assistant 含 tool_calls + tool 角色消息；失败结果 is_error=True）（验证：mock_requests.log 检查 / `tests/test_provider_tools.py` 回放部分）
- [ ] 改文件：唯一匹配替换成功；零匹配与多匹配返回明确中文错误（含次数/行号），文件未被修改（验证：`python -m pytest tests/test_tools.py`）
- [ ] 工具超时、参数错误、IO 错误、命令非零退出码均不崩溃，错误作为结构化结果返回（验证：`python -m pytest tests/test_tools.py tests/test_runner.py`）
- [ ] 每轮只执行一个工具：多个工具调用只执行第一个，其余提示忽略（验证：provider 单测 + 端到端场景）
- [ ] 回灌与展示内容超 30000 字符截断并注明「已截断」（验证：`tests/test_tools.py` run_tool 截断用例）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -m compileall mewcode/ scripts/` 无输出）
- [ ] 全部单元测试通过（验证：`python -m pytest tests/` 全绿，无跳过）
- [ ] 既有行为回归：普通文本轮次、思考块渲染、ProviderError 面板、Ctrl+C 中断、双击退出均不受影响（验证：`python -m pytest tests/` + 端到端场景 3）

## 端到端场景（tmux）

- [ ] 场景 1（工具调用全流程）：启动 mock（`python scripts/mock_llm.py`），配置指向 mock，`tool-model` 返回 read_file 调用 → TUI 显示 `🔧 read_file` 与参数摘要 → 显示绿色「工具结果」面板（含文件内容）→ 回到输入行不崩溃
- [ ] 场景 2（命令确认）：mock 返回 run_command 调用 → 终端显示完整命令与确认提示；输入 `y` → 命令真实执行并展示输出；再次触发输入 `n` 或直接回车 → 命令未执行，展示「用户拒绝了该命令的执行」红色面板
- [ ] 场景 3（结果回灌）：场景 1 之后再发一条消息 → mock_requests.log 中第二条请求体含上一轮的 tool_use 块与 tool_result 块（Anthropic 协议）；OpenAI 协议侧为 tool_calls 与 tool 角色消息
- [ ] 场景 4（既有功能回归）：用非 tool-model 模型发普通问题 → 文本流式回复、思考块、计时提示与之前完全一致；流式中按一次 Ctrl+C → 显示「回复中断」面板并回到输入行（双击退出逻辑不受影响）
- [ ] 场景 5（异常路径）：mock 以 `tool-badjson-model` 返回坏 JSON 参数的工具调用 → 显示红色「工具失败」面板（参数解析失败原因），程序不崩溃，回到输入行
