# 工具系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/tools/__init__.py` | 导出 Tool/ToolResult/ToolError/ToolRegistry、create_registry |
| 新建 | `mewcode/tools/base.py` | Tool 抽象、ToolResult、run_tool 执行包装 |
| 新建 | `mewcode/tools/registry.py` | ToolRegistry 登记/查找 |
| 新建 | `mewcode/tools/files.py` | ReadFileTool / WriteFileTool / EditFileTool |
| 新建 | `mewcode/tools/search.py` | FindFilesTool / GrepCodeTool |
| 新建 | `mewcode/tools/shell.py` | RunCommandTool |
| 新建 | `mewcode/agent/__init__.py` | 导出 ToolRunner |
| 新建 | `mewcode/agent/runner.py` | ToolRunner：执行、确认拦截、回灌 |
| 新建 | `tests/test_tools.py` | 六工具 + run_tool 包装 |
| 新建 | `tests/test_registry.py` | 注册中心 |
| 新建 | `tests/test_provider_tools.py` | 双协议流解析 + 请求体 + 回放重建 |
| 新建 | `tests/test_session_tools.py` | 会话工具记录 |
| 新建 | `tests/test_runner.py` | ToolRunner 全路径 |
| 修改 | `mewcode/session.py` | ToolUse/ToolResultRecord、Message 扩展、add_tool_result |
| 修改 | `mewcode/provider/base.py` | ToolCall 事件、stream 签名加 tools |
| 修改 | `mewcode/provider/anthropic.py` | tools 请求体、tool_use 解析、回放重建 |
| 修改 | `mewcode/provider/openai.py` | tools 请求体、tool_calls 聚合、回放重建 |
| 修改 | `mewcode/cli.py` | create_registry → run 传入 |
| 修改 | `mewcode/tui/app.py` | 工具收集/展示/确认/结果面板 |
| 修改 | `scripts/mock_llm.py` | tool-model 工具调用流 |

## T1: 会话层扩展

**文件：** `mewcode/session.py`、`tests/test_session_tools.py`
**依赖：** 无
**步骤：**
1. 定义 `ToolUse(id, name, args)` 与 `ToolResultRecord(tool_use_id, content, is_error)` 两个 dataclass
2. `Message` 增加字段 `tool_uses: list[ToolUse] = field(default_factory=list)` 与 `tool_results: list[ToolResultRecord] = field(default_factory=list)`
3. `ChatSession.add_assistant` 增加 `tool_uses: list[ToolUse] | None = None` 参数
4. `ChatSession` 增加 `add_tool_result(record: ToolResultRecord)`：追加一条 `Message(role="user", text="", tool_results=[record])`
5. 写 `tests/test_session_tools.py`：验证 add_assistant 带 tool_uses、add_tool_result 追加 user 消息、默认字段为空列表

**验证：** `python -m pytest tests/test_session.py tests/test_session_tools.py` 全绿

## T2: 工具接口与执行包装

**文件：** `mewcode/tools/base.py`、`tests/test_tools.py`（run_tool 部分）
**依赖：** 无
**步骤：**
1. 定义 `ToolResult(ok: bool, content: str)` 与 `ToolError(Exception)`
2. 定义抽象类 `Tool`：`name`/`description`/`input_schema` 三个 ClassVar、`timeout: float = 30.0`、`requires_confirmation: bool = False`、抽象方法 `execute(self, args: dict) -> ToolResult`
3. 定义常量 `RESULT_MAX_CHARS = 30000`
4. 实现 `run_tool(tool, args) -> ToolResult`：守护线程执行 `tool.execute`，`join(tool.timeout)` 超时返回「工具执行超时（N 秒）」错误结果；捕获 `Exception` 返回「工具执行异常: {e}」错误结果；成功内容超 RESULT_MAX_CHARS 截断并附「…（已截断，共 X 字符）」
5. 创建 `tests/test_tools.py`，用假工具类覆盖三种路径：sleep 工具（timeout=0.1）→ 超时错误；抛异常工具 → 异常错误；返回超长内容工具 → 截断且含「已截断」

**验证：** `python -m compileall mewcode/tools/` 通过；`python -m pytest tests/test_tools.py` run_tool 部分全绿

## T3: 注册中心

**文件：** `mewcode/tools/registry.py`、`tests/test_registry.py`
**依赖：** T2
**步骤：**
1. 定义 `ToolRegistry`：`register(tool)`（重名抛 ToolError）、`get(name) -> Tool`（找不到抛 ToolError）、`list() -> list[Tool]`
2. 写 `tests/test_registry.py`：登记/查找/重名报错/未注册报错

**验证：** `python -m pytest tests/test_registry.py` 全绿

## T4: 文件工具

**文件：** `mewcode/tools/files.py`、`tests/test_tools.py`（文件工具部分）
**依赖：** T2
**步骤：**
1. `ReadFileTool`：name=`read_file`，参数 `{file_path: 必填, limit: 可选}`；`Path(file_path)` 相对 cwd 解析；读文本（utf-8，失败报错）；limit 截断行数并注明；文件不存在 → 错误结果「文件不存在: {path}」
2. `WriteFileTool`：name=`write_file`，参数 `{file_path, content}`；创建父目录；覆盖写入；成功结果「已写入 {path}（{bytes} 字节）」
3. `EditFileTool`：name=`edit_file`，参数 `{file_path, old_text, new_text}`；`old_text` 为空 → 错误；0 次匹配 → 错误「原文未找到，请先 read_file 确认」；N>1 次 → 错误「原文匹配 N 次（行号: …），请提供更长上下文」；唯一匹配 → 替换写回，成功结果含行号
4. `tests/test_tools.py` 用 tmp_path：读成功/不存在/limit；写新建/覆盖；改唯一/零匹配/多匹配且文件未被改动

**验证：** `python -m pytest tests/test_tools.py` 文件工具部分全绿

## T5: 搜索工具

**文件：** `mewcode/tools/search.py`、`tests/test_tools.py`（搜索工具部分）
**依赖：** T2
**步骤：**
1. `FindFilesTool`：name=`find_files`，参数 `{pattern: 必填, path: 可选缺省 "."}`；`Path(path).glob(pattern)`；返回排序相对路径列表（`\n` 连接），上限 500 条超出注明「…（已截断，共 X 条）」；0 条 → 成功结果「未找到匹配文件」
2. `GrepCodeTool`：name=`grep_code`，参数 `{pattern: 必填, path: 可选, case_sensitive: 可选缺省 true}`；正则编译失败 → 错误结果；`rglob` 遍历跳过隐藏目录与二进制文件（读失败静默跳过）；逐行匹配输出 `相对路径:行号: 内容`；上限 200 行超出注明
3. `tests/test_tools.py` 用 tmp_path：glob 匹配/无结果；grep 命中多行/大小写/正则错误

**验证：** `python -m pytest tests/test_tools.py` 搜索工具部分全绿

## T6: 命令执行工具

**文件：** `mewcode/tools/shell.py`、`tests/test_tools.py`（shell 部分）
**依赖：** T2
**步骤：**
1. `RunCommandTool`：name=`run_command`，参数 `{command: 必填}`；`requires_confirmation = True`
2. `execute`：`subprocess.run(command, shell=True, capture_output=True, text=True, timeout=max(self.timeout - 2.0, 0.5))`（略小于工具通用超时，超时杀进程兜底；跟随 self.timeout 便于测试调整）；结果内容 = stdout + stderr 合并（带标记）+ 退出码说明；退出码非零 → `ok=False`；`TimeoutExpired` → 错误结果「命令超时已终止」
3. `tests/test_tools.py`：`echo hello` 成功；`exit 1` 失败含退出码；超时用例先把 `RunCommandTool.timeout` 临时设为 1.0 再执行 `sleep 5`（1 秒出结果，测完恢复）

**验证：** `python -m pytest tests/test_tools.py` shell 部分全绿

## T7: tools 包导出与注册

**文件：** `mewcode/tools/__init__.py`
**依赖：** T2-T6
**步骤：**
1. 导出 Tool、ToolResult、ToolError、ToolRegistry、run_tool 与六个工具类
2. 实现 `create_registry() -> ToolRegistry`：注册六个工具并返回

**验证：** `python -c "from mewcode.tools import create_registry; r = create_registry(); assert len(r.list()) == 6; print(r.list())"` 输出六个工具

## T8: provider 请求体携带 tools

**文件：** `mewcode/provider/base.py`、`mewcode/provider/anthropic.py`、`mewcode/provider/openai.py`
**依赖：** T2
**步骤：**
1. base.py：定义 `ToolCall(id, name, args, parse_error=None)`；`StreamEvent` 增加 `ToolCall`；`Provider.stream` 签名改为 `stream(self, messages, tools: list[Tool])`（导入 mewcode.tools.base 的 Tool）
2. anthropic.py：签名同步；`_build_payload` 增加 `tools` 参数并输出 `[{"name","description","input_schema"}]`；流解析暂不变（ToolCall 解析在 T9）
3. openai.py：签名同步；`_build_payload` 增加 `tools` 参数并输出 `[{"type":"function","function":{"name","description","parameters"}}]`
4. 现有调用点会暂时编译失败——`tui/app.py` 的 `provider.stream(session.messages)` 调用处临时补 `tools=[]` 占位（T13 换真值），`tests/test_providers.py` 同样补空列表

**验证：** `python -m compileall mewcode/` 通过；`python -m pytest tests/test_providers.py` 全绿（mock 收到的请求体新增 tools 字段不影响旧断言）

## T9: Anthropic 工具调用流解析

**文件：** `mewcode/provider/anthropic.py`、`tests/test_provider_tools.py`（anthropic 部分）
**依赖：** T8
**步骤：**
1. `_iter_events` 增加 tool_use 处理：`content_block_start` 的 `content_block.type == "tool_use"` 时记录 id/name、开启累积；`content_block_delta` 的 `delta.type == "input_json_delta"` 追加 `delta["partial_json"]`；`content_block_stop` 时 `json.loads` 累积串 → `ToolCall(id, name, args)`，解析失败 → `parse_error` 带原始串摘要
2. `Done` 不受影响；`message_stop` 仍产出 Done
3. `tests/test_provider_tools.py`：构造分片事件列表（id/name 起始块 + 多个 input_json_delta + stop + message_stop）喂给 `AnthropicProvider._iter_events`，断言产出 ToolCall 且 args 完整；另造一个坏 JSON 流断言 parse_error

**验证：** `python -m pytest tests/test_provider_tools.py` anthropic 部分全绿

## T10: OpenAI 工具调用碎片聚合

**文件：** `mewcode/provider/openai.py`、`tests/test_provider_tools.py`（openai 部分）
**依赖：** T8
**步骤：**
1. `_iter_events` 增加 tool_calls 聚合：`choices[0].delta.tool_calls` 按 `index` 存进有序字典，首个碎片取 `id` 与 `function.name`，`function.arguments` 碎片串接；`finish_reason == "tool_calls"` 时逐条 `json.loads` → `ToolCall`，解析失败 → parse_error；随后产出 Done
2. `tests/test_provider_tools.py`：构造带 index 的分片 chunks（arguments 分 3 片）断言拼接结果；坏 JSON 断言 parse_error

**验证：** `python -m pytest tests/test_provider_tools.py` openai 部分全绿

## T11: 回放重建（两家协议）

**文件：** `mewcode/provider/anthropic.py`、`mewcode/provider/openai.py`、`tests/test_provider_tools.py`（回放部分）
**依赖：** T1、T9、T10
**步骤：**
1. anthropic `_build_payload`：assistant 消息 content 先放 thinking 块，再放 `{"type":"tool_use","id","name","input"}` 块（来自 m.tool_uses），最后 text 块；含 `tool_results` 的 user 消息 content 为 `{"type":"tool_result","tool_use_id","content","is_error"}` 列表（text 为空则不拼 text 块）
2. openai `_build_payload`：assistant 消息带 tool_uses 时输出 `content: null` + `tool_calls: [{id, type:"function", function:{name, arguments: json.dumps(args)}}]`；含 `tool_results` 的消息按每条输出 `{"role":"tool","tool_call_id","content"}`（一条记录一条消息）
3. `tests/test_provider_tools.py`：构造含 ToolUse/ToolResultRecord 的会话，断言两家 `_build_payload` 输出结构精确匹配（含 is_error 回传）

**验证：** `python -m pytest tests/test_provider_tools.py` 回放部分全绿

## T12: ToolRunner

**文件：** `mewcode/agent/runner.py`、`mewcode/agent/__init__.py`、`tests/test_runner.py`
**依赖：** T1、T3
**步骤：**
1. `ToolRunner(registry, session, confirm: Callable[[str], bool] | None = None)`
2. `run(call: ToolCall) -> ToolResult` 流程：parse_error → 错误结果（不执行）；`registry.get` 抛 ToolError → 错误结果；`tool.requires_confirmation` 且 confirm 存在且 `confirm(命令文本)` 为 False → 「用户拒绝了该命令的执行」错误结果；否则 `run_tool(tool, call.args)`；无论成败最后 `session.add_tool_result(ToolResultRecord(...))` 回灌；返回结果
3. `tests/test_runner.py`：未知工具、parse_error、confirm 拒绝（命令未执行且回灌拒绝信息）、confirm 通过执行成功、执行失败回灌 is_error=True、无 confirm 时需确认工具直接执行

**验证：** `python -m pytest tests/test_runner.py` 全绿

## T13: CLI 装配

**文件：** `mewcode/cli.py`、`mewcode/tui/app.py`（装配部分）
**依赖：** T7、T12
**步骤：**
1. cli.py：`create_registry()` → `run(provider, config, registry)`
2. app.py `run()`：签名加 `registry: ToolRegistry`；创建 `ToolRunner(registry, session)`（confirm 回调在 T15 接入，暂传 None）；`_chat_round` 签名加 registry/runner；`provider.stream` 调用改为 `provider.stream(session.messages, registry.list())`（替换 T8 的 `tools=[]` 占位）

**验证：** `python -m compileall mewcode/` 通过；tmux 启动 MewCode 对 mock 发一条普通消息，回复正常渲染（无工具调用路径不受影响）

## T14: TUI 工具调用展示

**文件：** `mewcode/tui/app.py`
**依赖：** T13
**步骤：**
1. `_chat_round` 收集 ToolCall：第一个存入 `first_call`，其余计数 `ignored_count`；ToolCall 事件到达时不清 Live（正文/思考照常渲染）
2. Done 后若 `first_call` 存在：`session.add_assistant(text, thinking, thinking_raw, tool_uses=[ToolUse(...)])`；console 打印 `🔧 {name}` 行 + 参数摘要（`json.dumps(args, ensure_ascii=False)` 截断 200 字符，dim 样式）；`ignored_count > 0` 时打印「另有 N 个工具调用本步忽略」提示；`runner.run(first_call)`；打印结果面板（成功 `ok=True` 绿色「工具结果」/失败红色「工具失败」，内容截断 2000 字符）
3. 无工具调用轮次走原路径（add_assistant 不带 tool_uses）

**验证：** `python -m compileall mewcode/` 通过；tmux 中 mock `tool-model` 未就绪前，先手动验证代码路径可运行（发普通消息不报错）

## T15: 命令确认交互

**文件：** `mewcode/tui/app.py`
**依赖：** T14
**步骤：**
1. `run()` 内创建确认用 `PromptSession(message=[("class:prompt", "执行命令? [y/N] ")])` 与 confirm 回调：`confirm(cmd)` 先打印命令全文（bold），再 `prompt()`；输入 `y`/`Y` → True，其余（含空输入）→ False；Ctrl+C/EOF → False
2. `ToolRunner(registry, session, confirm=confirm)` 注入（替换 T13 的 None）
3. 注意 patch_stdout 已生效，确认提示正常显示在输出区

**验证：** tmux 中 mock `tool-model` 返回 run_command 调用：输入 y 看到命令执行结果；输入 n / 回车看到「用户拒绝了该命令的执行」；均回到输入行不崩溃

## T16: mock 工具调用流

**文件：** `scripts/mock_llm.py`
**依赖：** 无（可与 T14/T15 并行）
**步骤：**
1. 新增 `ANTHROPIC_TOOL_EVENTS`：tool_use 起始块（name=`read_file`，id=`toolu_mock_1`）+ 4 片 input_json_delta（参数 JSON 分片）+ content_block_stop + message_delta(stop_reason="tool_use") + message_stop
2. 新增 `OPENAI_TOOL_CHUNKS`：role 块 + 3 片带 index=0 的 arguments 碎片 + finish_reason="tool_calls" + `[DONE]`
3. 新增坏 JSON 变体：Anthropic 流与 OpenAI 流各一份（参数碎片拼不出合法 JSON），model 名为 `tool-badjson-model` 时返回
4. `do_POST`：model 为 `tool-model` / `tool-badjson-model` 且 body 含 `tools` 时返回对应工具流；其余分支不变

**验证：** 后台启动 mock，`curl -s -X POST http://127.0.0.1:8989/v1/messages -H 'content-type: application/json' -d '{"model":"tool-model","messages":[],"tools":[{}]}'` 输出含 tool_use 与 input_json_delta 的 SSE；chat/completions 同理

## T17: 测试收尾与全量验证

**文件：** 全部测试文件
**依赖：** T1-T16
**步骤：**
1. 跑全量 `python -m pytest tests/`，修复所有失败
2. 检查 `tests/test_providers.py` 旧用例对 tools 字段的兼容性（必要时调整断言）

**验证：** `python -m pytest tests/` 全绿，无跳过

## 执行顺序

```
T1 → T2 → T3 → T4/T5/T6（可并行）→ T7
                    ↘
T8 → T9 / T10（可并行）→ T11
                    ↘
T12 → T13 → T16 → T14 → T15
T17 收尾
```
