# Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `agent/events.py` | AgentEvent 事件类型、RoundEndReason |
| 新建 | `agent/collector.py` | StreamCollector 双路收集 |
| 新建 | `agent/executor.py` | execute_call 纯执行 |
| 新建 | `agent/loop.py` | Agent 循环主体、_stream_bridged、Plan Mode 常量 |
| 新建 | `agent/bridge.py` | run_in_background 线程桥 |
| 删除 | `agent/runner.py` | ToolRunner 被 executor + Agent 取代 |
| 修改 | `agent/__init__.py` | 导出 execute_call → Agent |
| 修改 | `provider/base.py` | UsageInfo；Done.usage |
| 修改 | `provider/anthropic.py` | message_delta usage 解析 |
| 修改 | `provider/openai.py` | stream_options + usage chunk 解析 |
| 修改 | `tools/base.py` | Tool.read_only 标记 |
| 修改 | `tools/files.py` | WriteFileTool/EditFileTool read_only=False |
| 修改 | `tools/shell.py` | RunCommandTool read_only=False |
| 修改 | `tui/commands.py` | /plan /do、帮助文本 |
| 修改 | `tui/app.py` | Agent 装配、_agent_round 事件消费渲染、删 confirm |
| 修改 | `scripts/mock_llm.py` | loop/multi-tool/unknown/never-stop 模型流 |
| 新建 | `tests/test_collector.py` | 双路收集测试 |
| 新建 | `tests/test_executor.py` | 取代 test_runner.py |
| 新建 | `tests/test_agent_loop.py` | 核心循环测试 |
| 新建 | `tests/test_plan_mode.py` | plan mode + 命令测试 |
| 修改 | `tests/test_provider_tools.py` | 两家 usage 解析测试 |
| 修改 | `tests/test_providers.py` | OpenAI stream_options 断言 |
| 修改 | `tests/test_tools.py` | read_only 标记断言 |
| 删除 | `tests/test_runner.py` | 被 test_executor.py 取代 |

## T1: UsageInfo 与 Done.usage

**文件：** `mewcode/provider/base.py`
**依赖：** 无
**步骤：**
1. 新增 `UsageInfo` dataclass：`input_tokens: int`、`output_tokens: int`
2. `Done` 增加 `usage: UsageInfo | None = None` 字段
**验证：** `python -m pytest tests/test_providers.py tests/test_provider_tools.py -q` 既有测试全绿（向后兼容）

## T2: Anthropic usage 解析

**文件：** `mewcode/provider/anthropic.py`、`tests/test_provider_tools.py`
**依赖：** T1
**步骤：**
1. `_iter_events` 中解析 `message_delta` 事件：`usage.input_tokens` / `usage.output_tokens`（缺省 0）累积到局部变量
2. `message_stop` 时 `Done(thinking_raw=..., usage=UsageInfo(...))`；流中无 usage 时为 `None`
3. `test_provider_tools.py` 增加测试：`_iter_events` 喂含 message_delta usage 的事件序列 → Done.usage 正确；无 message_delta → usage 为 None
**验证：** `python -m pytest tests/test_provider_tools.py -q` 全绿

## T3: OpenAI usage 解析与 stream_options

**文件：** `mewcode/provider/openai.py`、`tests/test_provider_tools.py`、`tests/test_providers.py`
**依赖：** T1
**步骤：**
1. `_build_payload` 增加 `"stream_options": {"include_usage": True}`
2. `_iter_events`：`choices` 为空但带 `usage` 的最终 chunk 解析 `prompt_tokens`/`completion_tokens`；finish chunk 上附带 usage 同样解析；Done 带出，无 usage 时为 None
3. `test_provider_tools.py` 增加测试：usage chunk → Done.usage；无 usage chunk → None
4. `test_providers.py` 增加断言：请求体含 `stream_options.include_usage == True`
**验证：** `python -m pytest tests/test_provider_tools.py tests/test_providers.py -q` 全绿

## T4: Tool.read_only 标记

**文件：** `mewcode/tools/base.py`、`mewcode/tools/files.py`、`mewcode/tools/shell.py`、`tests/test_tools.py`
**依赖：** 无
**步骤：**
1. `Tool` 增加 `read_only: ClassVar[bool] = True`（默认只读，注释说明用途：plan 模式过滤 + 分批并发判定）
2. `WriteFileTool`/`EditFileTool`（files.py）与 `RunCommandTool`（shell.py）显式 `read_only = False`
3. `test_tools.py` 增加断言：六个工具 read_only 值 = 读三个 True、写/改/命令三个 False
**验证：** `python -m pytest tests/test_tools.py -q` 全绿

## T5: 事件类型

**文件：** `mewcode/agent/events.py`
**依赖：** 无
**步骤：**
1. 定义 `ToolCallStarted`、`ToolResultEvent`、`UsageEvent`、`ProgressEvent`、`RoundEndReason`（Enum：OK/LIMIT/CANCELLED/UNKNOWN_TOOL/ERROR）、`RoundEnd`（reason + message）
2. 定义 `AgentEvent` 联合类型：`TextDelta | ThinkingDelta | ToolCallStarted | ToolResultEvent | UsageEvent | ProgressEvent | RoundEnd`（TextDelta/ThinkingDelta/ToolCall 从 provider.base 导入，ToolResult 从 tools.base 导入）
**验证：** `python -m compileall mewcode/agent/` 通过

## T6: StreamCollector

**文件：** `mewcode/agent/collector.py`、`tests/test_collector.py`
**依赖：** 无
**步骤：**
1. 实现 `StreamCollector`：`add_text`/`add_thinking`/`set_thinking_raw` + `full_text`/`thinking_text`/`thinking_raw` 属性（text 用 list 拼接）
2. 新建 `test_collector.py`：多次 add 后 full_text 拼接正确、thinking 独立累积、thinking_raw 原样保存
**验证：** `python -m pytest tests/test_collector.py -q` 全绿

## T7: execute_call 取代 ToolRunner

**文件：** `mewcode/agent/executor.py`（新）、`mewcode/agent/runner.py`（删）、`mewcode/agent/__init__.py`（改）、`tests/test_executor.py`（新）、`tests/test_runner.py`（删）
**依赖：** 无
**步骤：**
1. 新建 `executor.py`：`execute_call(registry, call) -> ToolResult`——parse_error 非空 → 失败结果；`registry.get` 抛 ToolError → 失败结果（未知工具，含已注册列表）；否则 `run_tool(tool, call.args)`。不弹确认、不写会话
2. 删除 `runner.py`（ToolRunner）
3. `agent/__init__.py` 改为导出 `execute_call`
4. 改写测试：`test_runner.py` → `test_executor.py`——保留 parse_error/未知工具/正常执行/异常转失败；删除 confirm 拒绝/允许两条（确认机制已随 ToolRunner 移除），新增断言 `RunCommandTool.requires_confirmation is True`（标记保留待后续安全章节）
5. 删除 `test_runner.py`
**验证：** `python -m pytest tests/test_executor.py -q` 全绿；`grep -rn "ToolRunner\|agent.runner" mewcode/ tests/` 无残留引用

## T8: Agent 骨架与 Plan Mode

**文件：** `mewcode/agent/loop.py`、`tests/test_plan_mode.py`
**依赖：** T4、T5
**步骤：**
1. 常量：`MAX_ITERATIONS = 30`、`UNKNOWN_TOOL_LIMIT = 2`、`PLAN_MODE_ON_MESSAGE`/`PLAN_MODE_OFF_MESSAGE`（plan 提示文本）
2. `Agent.__init__(provider, registry, session, max_iterations=MAX_ITERATIONS)`；`cancel()`（`threading.Event.set`）；`enter_plan_mode()`/`exit_plan_mode()`（置/清标志 + 注入提示 user 消息，幂等：重复调用不重复注入）；`plan_mode` 属性；`_visible_tools()`（plan 模式过滤 read_only）
3. 实现 `_stream_bridged(provider, messages, tools)` 私有异步辅助：泵线程跑同步 `provider.stream()`，事件经 `loop.call_soon_threadsafe` 灌入 `asyncio.Queue`，线程内异常（含 ProviderError）以哨兵包装传入；消费侧用 `asyncio.wait_for` 轮询取消标志（≤100ms 察觉），取消时抛内部 `_Cancelled` 哨兵
4. 新建 `test_plan_mode.py`：enter/exit 幂等（session 消息数只增两条）、提示文本注入、plan_mode 属性、`_visible_tools` 收窄为三个只读工具
**验证：** `python -m pytest tests/test_plan_mode.py -q` 全绿；`python -m compileall mewcode/agent/` 通过

## T9: run() 基础循环

**文件：** `mewcode/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T1、T5、T6、T7、T8
**步骤：**
1. `run(user_text)` 异步生成器骨架：清取消标志 → `start_index` 记录 → `session.add_user(text)` → 循环体 → 异常处理
2. 单轮流程：`yield ProgressEvent(i)` → `async for` 消费 `_stream_bridged`（TextDelta/ThinkingDelta 双路：collector 累积 + yield 转发；ToolCall 收集；Done 取 thinking_raw/usage）→ `session.add_assistant(full_text, thinking, thinking_raw, tool_uses)` → usage 累加 `yield UsageEvent`
3. 停止条件 ①⑤：calls 为空 → `RoundEnd(OK)` 结束；`ProviderError` → 回滚 `del session.messages[start_index:]` → `RoundEnd(ERROR, str(e))`；其他异常 → 同样回滚 + `RoundEnd(ERROR, "内部错误: …")` 兜底
4. `test_agent_loop.py` 写 FakeProvider（脚本化事件序列）与首批测试：无工具单轮正常结束（事件序列：Progress→Text→Usage→RoundEnd(OK)）、多轮推进至正常结束、中间轮文本落盘、中间轮 assistant 消息含 tool_uses、usage 跨轮累计、ProviderError 回滚（session 回到 start_index、RoundEnd(ERROR)）、普通异常兜底
**验证：** `python -m pytest tests/test_agent_loop.py -q` 全绿

## T10: 分批执行与剩余停止条件

**文件：** `mewcode/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T9
**步骤：**
1. 分批执行：calls 按 read_only 分两组；只读组逐个 `yield ToolCallStarted` 后 `asyncio.gather(asyncio.to_thread(execute_call, c) for c in 只读组)`；副作用组逐个 `yield ToolCallStarted` → `await asyncio.to_thread(execute_call, c)`；取消检查（执行后、回灌前）→ 取消则丢弃结果 `RoundEnd(CANCELLED)`
2. 回灌：全部执行完按原始调用顺序 `session.add_tool_result(...)` + `yield ToolResultEvent`（N7）
3. 停止条件 ②③④：连续未知工具计数（execute_call 前的 `registry.get` 探测，任何已知工具清零，≥2 → `RoundEnd(UNKNOWN_TOOL)`）；循环走完未结束 → `RoundEnd(LIMIT)`；流中/循环首取消检查 → `RoundEnd(CANCELLED)`
4. 测试：分批——fake 工具记录起止时间与线程 ID（只读两个并发重叠、命令在只读完成后串行、回灌顺序=原始顺序）；连续 2 次未知工具停止（第 3 次前停）、已知工具清零不触发；迭代上限（`max_iterations=2` + 每轮都有工具调用的 FakeProvider → RoundEnd(LIMIT)）；取消（慢速 FakeProvider + `asyncio` 任务中 `agent.cancel()` → RoundEnd(CANCELLED)、已落盘轮次保留、进行中轮次不落盘）
**验证：** `python -m pytest tests/test_agent_loop.py -q` 全绿

## T11: 线程桥

**文件：** `mewcode/agent/bridge.py`、`mewcode/agent/__init__.py`、`tests/test_agent_loop.py`
**依赖：** T10
**步骤：**
1. `run_in_background(agent, text) -> queue.Queue`：`threading.Thread(daemon=True, name="agent-loop")` 跑 `asyncio.run(_pump(...))`；`_pump` `async for` 消费 `agent.run(text)` 并 `q.put(ev)`
2. `agent/__init__.py` 导出 `Agent` 与 `run_in_background`
3. 测试：`run_in_background` 跑完一轮，队列收全事件且以 RoundEnd 收尾
**验证：** `python -m pytest tests/test_agent_loop.py -q` 全绿；`python -m compileall mewcode/` 通过

## T12: /plan 与 /do 命令

**文件：** `mewcode/tui/commands.py`、`tests/test_plan_mode.py`
**依赖：** T8
**步骤：**
1. `handle_command(text, session, agent)` 增加 agent 参数：`/plan` → `agent.enter_plan_mode()` 反馈「已进入计划模式：仅只读工具可用，模型将先给出计划」；`/do` → `agent.exit_plan_mode()` 反馈「已切回执行模式」；重复执行反馈「已在计划模式」/「当前不在计划模式」
2. `COMMAND_NAMES` 加入 `/plan`、`/do`；`_HELP_TEXT` 增加两行说明
3. 测试：用 FakeAgent（记录方法调用）验证 `/plan`/`/do` 分发与反馈文本
**验证：** `python -m pytest tests/test_plan_mode.py -q` 全绿

## T13: TUI 事件消费渲染

**文件：** `mewcode/tui/app.py`
**依赖：** T11、T12
**步骤：**
1. `run()`：`session = ChatSession()` → `agent = Agent(provider, registry, session)`；删除 confirm PromptSession 与 confirm 回调（F6）；`handle_command(text, session, agent)` 传入
2. `_chat_round` 替换为 `_agent_round(console, agent, text, tracker)`：
   - `q = run_in_background(agent, text)`，循环 `q.get(timeout=0.1)` 消费
   - `ProgressEvent` → 开启本轮 Live（`_stream_renderable` 增加 iteration 参数：首行显示「第 N 轮」dim 文本）
   - `TextDelta`/`ThinkingDelta` → 累积 + `live.update`
   - `ToolCallStarted` → 关闭本轮 Live，打印 `🔧 工具名 + 参数摘要`（复用 ch04 的 200 字符截断）
   - `ToolResultEvent` → 打印结果面板（成功绿/失败红，2000 字符显示截断）
   - `UsageEvent` → 累加 input/output
   - `RoundEnd` → 关闭 Live；有用量时打印 dim 行「本轮 token: 输入 X · 输出 Y」；按 reason 打印面板：ERROR 红「请求失败」、CANCELLED 黄「已取消（已完成步骤保留）」、LIMIT 黄「已达迭代上限（30 轮）」、UNKNOWN_TOOL 黄「连续调用未知工具，已停止」、OK 无面板
   - `KeyboardInterrupt` → `agent.cancel()` → 继续排空队列直到 RoundEnd（面板显示已取消）→ `tracker.reset()`
3. Live 开合改用显式 enter/exit 管理（事件驱动下不再用 with 包裹整轮）
**验证：** `python -m pytest -q` 全量测试绿；`python -m compileall mewcode/` 通过

## T14: mock 多轮流

**文件：** `scripts/mock_llm.py`
**依赖：** 无
**步骤：**
1. 新增辅助函数：按请求体统计已回灌工具结果数（Anthropic 数 tool_result 块、OpenAI 数 tool 角色消息）
2. `loop-model`（两家）：第 1 轮（0 个结果）→ 文本「我先看看结构」+ read_file 调用（分片）；第 2 轮（1 个）→ edit_file 调用；第 3 轮（≥2）→ 纯文本「改好了」；message_delta / 最终 chunk 携带 usage
3. `multi-tool-model`：单轮 read_file + grep_code + run_command 三个调用（Anthropic 三个 tool_use 块、OpenAI 三个 index）
4. `unknown-tool-model`：每轮调用 `no_such_tool`
5. `never-stop-model`：每轮调用 read_file（参数有效）
6. 分发分支接进 do_POST（两家协议各一套）
**验证：** `python scripts/mock_llm.py 8788` 后台启动 → `curl` 模拟三轮请求（请求体 tool_result 数 0/1/2）→ 观察返回流与 `mock_requests.log` 记录 → 关闭进程

## T15: 全量回归

**文件：** 无（验证任务）
**依赖：** T1–T14
**步骤：**
1. `python -m pytest -q` 全量跑
2. 若有失败：修复后重跑，直至全绿
**验证：** 全部测试通过、无警告性失败

## T16: tmux 端到端冒烟

**文件：** 无（验证任务）
**依赖：** T15
**步骤：**
1. tmux 启动 `python scripts/mock_llm.py 8989`
2. tmux 启动 mewcode（配置指向 mock、model 为 loop-model），输入一段真实请求（如「看看项目结构，把 README 里的版本号改成 0.6.0，然后运行测试」）
3. 观察：多轮自动推进（第 N 轮进度）、工具调用与结果面板、最终回复、token 用量行
4. 验证 /plan → 提问 → /do 流程；验证循环中 Ctrl+C 取消提示
**验证：** 对照 checklist.md 逐项观察，记录证据

## 执行顺序

```
T1 ──→ T2、T3（可并行）
T4、T5、T6、T7（互相独立，可并行）
T8（依赖 T4、T5）──→ T9（依赖 T1、T5-T8）──→ T10 ──→ T11 ──→ T13
T12（依赖 T8）───────────────────────────────↗
T14（独立，可任意时点）
T15（依赖全部）──→ T16
```
