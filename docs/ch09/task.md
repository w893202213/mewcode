# 上下文管理 Tasks

> 说明：plan 中「cli.py 传 context_window」并入 T10——`tui/app.py` 的 `run()` 已持有 config，
> 直接从 `config.context_window` 取即可，cli.py 不动。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/context/__init__.py` | 包导出 |
| 新建 | `mewcode/context/estimator.py` | TokenEstimator：锚点 + 字符差分估算 |
| 新建 | `mewcode/context/store.py` | ResultStore、StoreError：存盘与预览 |
| 新建 | `mewcode/context/prompts.py` | 摘要 Prompt 构建与响应解析 |
| 新建 | `mewcode/context/compressor.py` | Compressor、SummarizeResult、SummarizeError |
| 修改 | `mewcode/config.py` | `context_window` 字段与校验 |
| 修改 | `mewcode/session.py` | `replace_head` |
| 修改 | `mewcode/agent/events.py` | `CompressEvent` |
| 修改 | `mewcode/agent/loop.py` | 接入两层压缩 + `compact_now` |
| 修改 | `mewcode/tui/commands.py` | `/compact` 命令 |
| 修改 | `mewcode/tui/app.py` | 渲染 CompressEvent + 传 `context_window` |
| 新建 | `tests/test_estimator.py` | 估算器测试 |
| 新建 | `tests/test_store.py` | 存盘测试 |
| 新建 | `tests/test_prompts.py` | Prompt 构建/解析测试 |
| 新建 | `tests/test_compressor.py` | 压缩器测试 |
| 新建 | `tests/test_context_loop.py` | Agent 集成测试 |
| 修改 | `tests/test_config.py` | context_window 用例 |
| 修改 | `tests/test_session.py` | replace_head 用例 |
| 修改 | `scripts/mock_llm.py` | 摘要模式（tools 为空时的响应分支） |

## T1: 配置层 context_window

**文件：** `mewcode/config.py`、`tests/test_config.py`
**依赖：** 无
**步骤：**
1. `ProviderConfig` 新增字段 `context_window: int = 200000`（dataclass 默认值）
2. `load_config` 解析 `item.get("context_window", 200000)`：值必须为正整数（`isinstance(value, int)` 且 `value > 0`，注意 YAML 布尔值 `isinstance(True, int)` 为真，须先排除 `bool`），否则抛 `ConfigError`，消息中文、指明第几份配置
3. `tests/test_config.py` 增用例：缺省取 200000；显式配置生效；`0`、`-5`、`"abc"`、`true` 抛 ConfigError

**验证：** `python -m pytest tests/test_config.py -q` 全部通过

## T2: 会话层 replace_head

**文件：** `mewcode/session.py`、`tests/test_session.py`
**依赖：** 无
**步骤：**
1. `ChatSession` 新增 `replace_head(end: int, new_messages: list[Message])`：删除 `messages[:end]`，把 `new_messages` 插入头部；`end` 越界抛 `ValueError`（中文消息）
2. `tests/test_session.py` 增用例：正常替换（尾段保留、新头部就位）；`end=0` 纯插入；越界抛错

**验证：** `python -m pytest tests/test_session.py -q` 全部通过

## T3: Token 估算器

**文件：** `mewcode/context/__init__.py`（新建，先空）、`mewcode/context/estimator.py`、`tests/test_estimator.py`
**依赖：** 无
**步骤：**
1. `TokenEstimator`：`CHARS_PER_TOKEN = 4` 类常量；`_anchor_tokens`/`_anchor_chars` 初始 None
2. `anchor(input_tokens, messages)`：记录 input_tokens 与当前消息字符总量
3. `estimate(messages, system) -> int`：
   - 无锚点：`(消息字符 + len(system.stable) + len(system.dynamic)) // 4`
   - 有锚点：`_anchor_tokens + max(0, 当前消息字符 − _anchor_chars) // 4`
4. `_message_chars(m)`（模块级函数）：`text + thinking + Σ tool_result.content + Σ json.dumps(tool_use.args, ensure_ascii=False)` 的字符数
5. `tests/test_estimator.py`：无锚点全量估算；锚点后增量估算（追加消息上升、`replace_head` 压掉头部后下降、不低于锚点值）；`_message_chars` 口径（含工具结果与参数）

**验证：** `python -m pytest tests/test_estimator.py -q` 全部通过

## T4: 结果存盘器

**文件：** `mewcode/context/store.py`、`tests/test_store.py`
**依赖：** 无
**步骤：**
1. `StoreError(Exception)`：中文消息
2. `ResultStore(base_dir: Path)`：构造时 `mkdir(parents=True, exist_ok=True)`；`save(content, seq)` 写 `tool_result_<seq>.txt`（seq 由调用方传入），返回 Path，IO 失败抛 `StoreError`（中文）
3. `preview(content, path, head_chars)`：返回「前 head_chars 字符 + 换行 + `…（完整内容已存盘，共 N 字符）`+ 换行 + `文件路径: <path>` + 换行 + `需要完整内容时请读取该文件`」格式文本（不足 head_chars 时不给省略号）
4. `tests/test_store.py`：存盘后文件存在且内容一致、序号递增文件名正确；预览格式断言；base_dir 为文件路径（非目录）时 `save` 抛 StoreError

**验证：** `python -m pytest tests/test_store.py -q` 全部通过

## T5: 摘要 Prompt 与解析

**文件：** `mewcode/context/prompts.py`、`tests/test_prompts.py`
**依赖：** 无
**步骤：**
1. 摘要指令常量 `SUMMARY_SYSTEM`（中文，stable 用）：明确「本请求用于压缩会话历史；**禁止调用任何工具**；先输出 `<分析草稿>` 段梳理历史要点，再输出 `<正式摘要>` 段；正式摘要必须按 6 个固定部分组织（总体目标与状态 / 已完成工作 / 关键文件与代码 / 重要事实与决策 / 未完成事项 / 注意事项）；正式摘要之外的输出一律忽略」
2. 边界提示常量 `BOUNDARY_NOTICE`：「以上为本次压缩生成的会话摘要，不是原始对话。涉及文件内容、代码细节时请重新读取文件确认，不要直接依据摘要中的代码片段修改文件。」
3. `build_summarize_request(messages_to_summarize: list[Message]) -> (SystemPrompt, list[Message])`：返回 `SystemPrompt(stable=SUMMARY_SYSTEM, dynamic="")` 与单条 user 消息（text = 逐条列出待摘要内容：`[用户消息]`/`[助手回复]`/`[工具结果]` 前缀 + 内容；用 `_message_chars` 超长时每条约 4000 字符截断，防止摘要请求本身过大）
4. `parse_summary(text) -> str`：提取 `<正式摘要>` 与 `</正式摘要>` 之间的内容并 strip；缺失或为空抛 `SummarizeError`（中文：「摘要响应格式不符：缺少 <正式摘要> 段」）
5. `tests/test_prompts.py`：请求消息为单条 user 且内容含待摘要原文；system.stable 含「禁止调用任何工具」与 6 个部分名；解析正常提取并丢弃草稿段；缺正式段/空正式段抛 SummarizeError

**验证：** `python -m pytest tests/test_prompts.py -q` 全部通过

## T6: 压缩器

**文件：** `mewcode/context/compressor.py`、`mewcode/context/__init__.py`（填导出）、`tests/test_compressor.py`
**依赖：** T2、T3、T4、T5
**步骤：**
1. `SummarizeError(Exception)`；`SummarizeResult` dataclass（compacted_messages / kept_user_messages / est_saved_tokens）
2. `Compressor.__init__` 按 plan 参数表（全部带默认值，仅 provider/estimator/store/context_window 必填）
3. `prevent(msg: Message) -> CompressEvent | None`：
   - 遍历 `msg.tool_results`：单条 content 超 `result_threshold` 时 `store.save(content, seq)`（seq 内部自增）并把 content 替换为 `store.preview(...)`
   - 多条合计超阈值时按 content 长度从大到小依次存盘替换，直到合计低于阈值
   - 存盘抛 `StoreError` 时返回 None（降级，消息保持原文）
   - 成功返回 `CompressEvent(kind="prevent", message=中文提示含路径, path=...)`
4. `summarize(session, manual=False) -> SummarizeResult`：
   - 保留区：从尾部逐条按 `_message_chars/4` 累加，≤ `keep_tokens` 且条数 ≥ `min_keep_messages`（消息不足 5 条时全部保留）
   - 摘要区为空时直接返回 `SummarizeResult(0, 0, 0)`（不调 LLM）
   - 摘要区中：`role=user` 且无 tool_results 的消息原文保留（kept_user_messages）；其余消息进摘要输入
   - `build_summarize_request` → `provider.stream(请求消息, [], 请求system)` 同步消费全部 TextDelta → `parse_summary`
   - `session.replace_head(保留区起点, 保留的用户原文 + [摘要消息(user)] + [边界消息(user)])`
   - 统计：est_saved_tokens = 压缩前估算 − 压缩后估算（调用 estimator.estimate）
   - 任何异常（ProviderError、SummarizeError 等）→ 连续失败计数 +1，达到 3 次熔断，包装为 `SummarizeError` 抛出；成功计数清零
5. `needs_auto(messages, system)`：`estimate ≥ context_window − auto_margin` 且未熔断；`breaker_open` 属性：失败计数 ≥ 3
6. `tests/test_compressor.py`（用 FakeProvider 式 stub，含 `summarize` 用 handler 返回带草稿段+正式段的文本流）：
   - prevent：单条超阈值（文件存在、消息含预览与路径）；合计超阈值从大到小；存盘失败返回 None 消息不变
   - summarize：会话重构形态（用户原文在前、摘要消息、边界消息、保留区不动）；摘要区空直接返回；保留区 ≥5 条；est_saved_tokens > 0
   - 熔断：摘要调用失败 3 次后 breaker_open 为真、needs_auto 为假；成功一次后清零
7. `context/__init__.py` 导出 Compressor、TokenEstimator、ResultStore、StoreError、SummarizeError、SummarizeResult

**验证：** `python -m pytest tests/test_compressor.py -q` 全部通过

## T7: 压缩事件

**文件：** `mewcode/agent/events.py`
**依赖：** 无
**步骤：**
1. 新增 `CompressEvent` dataclass：kind（"prevent" | "summarize_start" | "summarize_done" | "summarize_failed" | "breaker"）、message、path、compacted_messages、est_saved_tokens（按 plan 默认值）
2. 加入 `AgentEvent` 联合类型

**验证：** `python -m compileall mewcode/agent/events.py` 通过

## T8: Agent Loop 接入

**文件：** `mewcode/agent/loop.py`、`tests/test_context_loop.py`
**依赖：** T6、T7
**步骤：**
1. `Agent.__init__` 新增参数 `context_window: int = 200000`、`context_dir: Path | None = None`；构造 `TokenEstimator`、`ResultStore(context_dir 或 Path.cwd()/".mewcode"/"context")`、`Compressor`
2. 回灌处（add_tool_result 之后、yield ToolResultEvent 之前）：对新消息调 `prevent`，事件非 None 则 yield
3. 每轮请求前（`system = self._builder.build(...)` 之后）：
   - `needs_auto(messages, system)` 为真 → yield `CompressEvent(kind="summarize_start")` → `await asyncio.to_thread(compressor.summarize, session, False)` → 成功 yield `summarize_done`（含统计）；`SummarizeError` → yield `summarize_failed`（消息含错误），**继续发请求**
   - `estimator.estimate ≥ 窗口 − auto_margin` 且 `breaker_open` → yield `CompressEvent(kind="breaker", message=中文提示已熔断)`
4. `Done` 事件处理处：`estimator.anchor(event.usage.input_tokens, self._session.messages)`
5. `compact_now() -> str`：无条件 `summarize(session, manual=True)`，成功返回中文统计反馈、失败返回「压缩失败: 原因」（不抛异常）
6. `tests/test_context_loop.py`（复用 FakeProvider 模式，`Agent(..., context_window=小值, context_dir=tmp_path)`）：
   - 自动触发：handler 前几轮返回 ToolCall（大结果工具）+ `Done(usage=UsageInfo(...))`；断言事件流出现 summarize_start/summarize_done；摘要调用时 `tools == []`（AC6）；后续请求的 messages 形态为 [用户原文] + 摘要 + 边界 + 保留区
   - `/compact` 入口：`agent.compact_now()` 返回中文反馈且会话被压缩
   - 熔断：摘要调用抛 ProviderError 三次 → 事件流出现 breaker；`compact_now()` 仍可发起且成功后熔断解除

**验证：** `python -m pytest tests/test_context_loop.py tests/test_agent_loop.py -q` 全部通过（既有循环行为不回归）

## T9: /compact 命令

**文件：** `mewcode/tui/commands.py`
**依赖：** T8
**步骤：**
1. `COMMAND_NAMES` 加 `"/compact"`
2. `_HELP_TEXT` 加一行：`"  /compact  压缩对话上下文：较早的消息生成摘要，近期消息原文保留"`
3. `handle_command` 分支：`command == "/compact"` → 返回 `(CommandResult.CONTINUE, agent.compact_now())`

**验证：** `python -m compileall mewcode/tui/commands.py` 通过；手工构造 Agent 调 `handle_command("/compact", session, agent)` 返回压缩反馈

## T10: TUI 渲染与装配

**文件：** `mewcode/tui/app.py`
**依赖：** T7、T8
**步骤：**
1. `run()` 创建 Agent 时传 `context_window=config.context_window`
2. `_consume_events` 与 `_drain_cancelled` 新增 `CompressEvent` 分支：灰色提示行（`style=_TIMER_STYLE`），summarize_done 附带「已压缩 N 条消息，估算节省 M token」，prevent 附「结果已存盘至 <path>」

**验证：** `python -m compileall mewcode/tui/app.py` 通过

## T11: mock_llm 摘要模式 + e2e 联调

**文件：** `scripts/mock_llm.py`
**依赖：** T1–T10
**步骤：**
1. mock_llm 增摘要分支：`has_tools` 为假（tools 为空）时返回文本轮——内容为 `<分析草稿>…</分析草稿><正式摘要>` 六部分固定文本 `</正式摘要>`（Anthropic 与 OpenAI 两条协议路径都加）
2. 准备临时 config：`context_window: 2000`（小窗口触发）+ mock 模型（复用 ch08 的 tool-model 或文本轮模型）
3. tmux 启动 MewCode，连续对话数轮（含工具调用产生大结果），观察：轻量预防提示（大结果存盘）、自动压缩提示（summarize_start/done）、压缩后继续正常工作
4. 输入 `/compact` 观察手动压缩反馈；`/help` 中出现 /compact

**验证：** 对照 `docs/ch09/checklist.md` 逐项验收（T11 完成后进入阶段六）

## 执行顺序

```
T1 ─┐
T2 ─┼─→ T6 ──┐
T3 ─┤        ├─→ T8 ──→ T9 ──┐
T4 ─┤        │   ↑           ├─→ T11
T5 ─┘        │   T7 ─→ T10 ──┘
             └─────────────┘
T1–T5 全部可并行；T7 与 T6 可并行；T9、T10 在 T8 后可并行
```
