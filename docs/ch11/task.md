# ch11 记忆系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/session.py` | 增 `ChatSession.restore()` |
| 新建 | `mewcode/memory/instructions.py` | 三层指令加载 + @include 展开 |
| 新建 | `mewcode/memory/sessions.py` | SessionStore / SessionLog / 恢复加载 / 时间提醒 |
| 新建 | `mewcode/memory/notes.py` | NoteManager / NoteUpdater / build_memory_block |
| 新建 | `mewcode/memory/prompts.py` | 笔记更新 LLM 提示与响应解析 |
| 新建 | `mewcode/memory/__init__.py` | MemoryContext / build_memory_context |
| 修改 | `mewcode/prompt/builder.py` | SystemPromptBuilder 增 memory_loader |
| 修改 | `mewcode/agent/loop.py` | Agent 增 memory_loader 透传 + maybe_compact_on_restore |
| 修改 | `mewcode/cli.py` | --resume 参数、三层指令加载、记忆上下文装配 |
| 修改 | `mewcode/tui/commands.py` | /resume 登记到命令名与帮助 |
| 修改 | `mewcode/tui/app.py` | 欢迎面板恢复信息、轮后落盘+笔记触发、提示队列、/resume 交互 |
| 新建 | `tests/test_memory_instructions.py` | 指令加载测试 |
| 新建 | `tests/test_memory_sessions.py` | 存档/恢复测试 |
| 新建 | `tests/test_memory_notes.py` | 笔记管理/更新器测试 |
| 新建 | `tests/test_memory_integration.py` | 装配与注入集成测试 |
| 修改 | `tests/test_session.py` | restore 测试 |
| 修改 | `tests/test_prompt.py` | 记忆块注入测试 |

---

## T1: ChatSession.restore

**文件：** `mewcode/session.py`、`tests/test_session.py`
**依赖：** 无
**步骤：**
1. 在 `ChatSession` 增 `restore(self, messages: list[Message]) -> None`：整体替换 `self.messages`（入参拷贝成新 list）。
2. `tests/test_session.py` 增测试：restore 后消息列表与入参一致、restore 覆盖旧历史、restore 后再 add_user 正常追加。

**验证：** `python -m pytest tests/test_session.py -q` 全部通过。

---

## T2: 三层指令加载（基础版）

**文件：** `mewcode/memory/instructions.py`、`tests/test_memory_instructions.py`
**依赖：** T1
**步骤：**
1. 定义 `InstructionResult`（dataclass：`text: str`、`warnings: list[str]`）。
2. 实现 `load_instructions(project_root: Path, user_dir: Path) -> InstructionResult`：
   - 依次读三层：`project_root/MEWCODE.md`、`project_root/.mewcode/instructions.md`、`user_dir/instructions.md`，非空内容用 `\n\n` 按序拼接；
   - 文件不存在：静默跳过（不产生警告）；
   - 读取失败（OSError / UnicodeDecodeError）：该层按空处理，warnings 追加「指令文件 X 读取失败，已跳过: <原因>」。
3. 内部辅助 `_read_text_safe(path) -> tuple[str, str | None]`（返回内容与错误消息）。
4. 测试：三份文件各写不同内容 → 拼接顺序为 项目根 → 项目 .mewcode → 用户目录；缺层跳过无警告；损坏文件（写入非法字节）→ 警告且启动不抛。

**验证：** `python -m pytest tests/test_memory_instructions.py -q` 全部通过。

---

## T3: @include 展开

**文件：** `mewcode/memory/instructions.py`、`tests/test_memory_instructions.py`
**依赖：** T2
**步骤：**
1. 实现递归展开 `_expand(path, project_root, user_dir, scope, depth, visited, warnings) -> str`，scope 为 `"project" | "user"`：
   - 逐行扫描，行首（允许前导空白）匹配 `@include <路径>`（路径可被单/双引号包裹）；非匹配行原样保留；
   - 相对路径按当前文件所在目录解析后 `resolve()`；
   - 嵌套深度：顶层文件 depth=1，include 目标 depth+1；depth > 3 → 该行跳过，warnings 追加「include 嵌套超过 3 层，已跳过: X」；
   - 全局 visited 集合（跨调用共享？不——每次 load_instructions 调用内新建）：已加载过 → 静默跳过（防环）；加载成功加入 visited；
   - 越界拦截：scope=project 的目标必须在 project_root 内（`is_relative_to`）；scope=user 的目标必须在 user_dir 内；越界 → 跳过，warnings 追加「include 目标超出允许目录，已跳过: X」；
   - 目标不存在 → 跳过，warnings 追加「include 目标不存在: X」；
   - 展开：include 行替换为目标文件的展开文本。
2. `load_instructions` 改为：三层文件分别以对应 scope（前两层 project、第三层 user）调用 `_expand`，depth=1。
3. 测试：include 正常内联展开；相对路径基于当前文件目录；A→B→A 循环不死循环（B 只加载一次）；嵌套 4 层被截断且警告；项目级文件 include 项目外路径 → 跳过+警告；用户级文件 include ~/.mewcode 内文件正常、include 外部路径跳过+警告；引号路径可用。

**验证：** `python -m pytest tests/test_memory_instructions.py -q` 全部通过。

---

## T4: 会话存档基础（ID/追加/扫描/清理）

**文件：** `mewcode/memory/sessions.py`、`tests/test_memory_sessions.py`
**依赖：** T1
**步骤：**
1. 序列化函数 `message_to_line(m: Message, ts: float) -> dict`：
   - user 行：`{"type": "user", "text", "summary", "ts"}`；
   - assistant 行：`{"type": "assistant", "text", "thinking", "thinking_raw", "tool_uses": [{"id","name","args"}], "summary", "ts"}`；
   - tool_result 行：每条记录一行 `{"type": "tool_result", "tool_use_id", "content", "is_error", "ts"}`。
2. `SessionLog`：`__init__(path)` 以追加模式打开（`open(path, "a", encoding="utf-8")`，不截断）；`append_messages(messages, ts)` 每消息 `json.dumps(ensure_ascii=False)` 一行 + `flush()`。
3. `SessionStore`：`__init__(directory)` 建目录（exist_ok）；`new_log() -> tuple[str, SessionLog]`：ID = `time.strftime("%Y%m%d-%H%M%S")` + "-" + 4 位随机（小写字母+数字），目标文件已存在则重试（最多 10 次，仍撞车抛 RuntimeError）。
4. `list_recent(n=5) -> list[SessionSummary]`：扫 `*.jsonl`，逐行读到第一条有效 user 文本做标题（截断 50 字符，超长加 "…"），消息数=有效行数，按 mtime 倒序取前 n。坏行不计入有效行、不打断扫描。
5. `cleanup_expired(days=30) -> int`：mtime 早于 `now - days*86400` 的文件删除，返回删除数。
6. 测试：ID 格式与撞车重试（预建同 ID 文件）；追加写行数与行内容往返；list_recent 标题截断/计数/倒序；清理只删过期文件。

**验证：** `python -m pytest tests/test_memory_sessions.py -q` 全部通过。

---

## T5: 恢复加载（坏行/截断/现算）

**文件：** `mewcode/memory/sessions.py`、`tests/test_memory_sessions.py`
**依赖：** T4
**步骤：**
1. `_parse_line(line: str) -> Message | None`：JSON 解析失败 → None；type 非三选一 → None；user 行缺 text 字段 → None；assistant 行 text/thinking 缺省 ""、thinking_raw/tool_uses 缺省空、逐项还原 ToolUse；tool_result 行缺 tool_use_id 或 content → None。
2. `SessionStore.load(session_id) -> LoadResult`：
   - 文件不存在抛 `FileNotFoundError`；
   - 逐行解析，坏行计数跳过；有效行转 `Message` 存入缓冲；
   - 截断：读到带 tool_uses 的 assistant 行后进入配平状态——向后收集 tool_result 行的 tool_use_id 集合，直到全部 id 配平则转出；若先遇到 EOF 或非 tool_result 行仍未配平 → 丢弃该 assistant 行及缓冲中其后所有行，`truncated=True`，扫描结束；
   - `title` = 首条有效 user 文本截断 50 字符；`last_ts` = 最后一条有效行 ts；`skipped_lines` = 坏行数。
3. `maybe_add_time_gap_notice(messages, last_ts, now, days=7) -> bool`：`last_ts` 存在且 `now - last_ts > days*86400` 时，追加 `Message(role="user", text=提醒文本, summary=True)`，提醒文本含原最后活动日期与当前日期；返回是否插入。
4. 测试：完整往返保真（thinking_raw 含 dict、tool_uses 列表）；坏行跳过计数正确；未配平 assistant 尾巴被截断（assistant 行之前内容保留）；已配平完整轮次保留；末尾孤立 user 行保留；title 截断；时间提醒阈值内不插入、超阈值插入且带 summary 标记。

**验证：** `python -m pytest tests/test_memory_sessions.py -q` 全部通过。

---

## T6: NoteManager 与记忆块拼接

**文件：** `mewcode/memory/notes.py`、`tests/test_memory_notes.py`
**依赖：** 无（可并行）
**步骤：**
1. `NoteChange` dataclass：`action`（"add"/"update"/"none"）、`note_type`、`scope`（"user"/"project"）、`name`、`description`、`content`。
2. `NoteManager(directory)`：建目录；
   - `index_text() -> str`：读 `index.md`，缺失返回 ""；
   - `load_notes() -> str`：glob `*.md`（排除 index.md），每条拼「`--- 笔记: <name>` + 正文（截断 1500 字符）」；
   - `write_note(change) -> None`：文件名 `<name>.md`，name 不匹配 `^[a-z0-9][a-z0-9-]*$` 时改用 `note-<int(time.time())>`；frontmatter：name/description/type/updated（`date.today().isoformat()`）+ 空行 + 正文；
   - `write_index(text) -> None`：写盘前截断——超 200 行取前 200 行、超 25KB 按字节截到最后一个完整行。
3. `build_memory_block(user_index: str, project_index: str) -> str`：非空侧拼「`## 用户级记忆` / `## 项目级记忆`」小节；都为空返回 ""。
4. 测试：笔记写读往返（frontmatter 字段齐全）；非法 name 自动换名；索引行数/字节截断；load_notes 排除 index.md 且截断；block 单侧/双侧/全空。

**验证：** `python -m pytest tests/test_memory_notes.py -q` 全部通过。

---

## T7: 笔记更新提示模板与解析

**文件：** `mewcode/memory/prompts.py`、`tests/test_memory_notes.py`
**依赖：** 无（可并行）
**步骤：**
1. `NOTES_SYSTEM_PROMPT`：中文说明四分类含义（用户偏好/纠正反馈/项目知识/参考资料）、去重规则（同主题更新而非新增、无新信息输出空 changes）、索引规则（每行 `- [name](name.md) — description`、保持 ≤200 行且 ≤25KB、超限时顺带精简）、输出格式：纯 JSON `{"changes": [...], "user_index": "...", "project_index": "..."}`，changes 每项含 action/type/scope/name/description/content。
2. `build_notes_request(recent, user_index, project_index, user_notes, project_notes) -> str`：user 消息文本含「最近对话」+ 用户级/项目级现有笔记 + 两份索引原文，要求模型按 schema 回答。
3. `NotesParseError` 异常类；`parse_notes_response(text) -> tuple[list[NoteChange], str, str]`：
   - 提取 JSON：整体 json.loads 失败时剥离 ```json 围栏重试，仍失败抛 NotesParseError；
   - 校验 changes 为 list，逐项校验 action/type/scope/name/description/content 字段齐全且 action 三选一、scope 二选一，非法项丢弃；
   - `user_index`/`project_index` 缺省按空串处理。
4. 测试：正常 JSON 解析；围栏包裹解析；缺字段条目被丢弃；整体非法抛 NotesParseError。

**验证：** `python -m pytest tests/test_memory_notes.py -q` 全部通过。

---

## T8: NoteUpdater 异步更新

**文件：** `mewcode/memory/notes.py`、`tests/test_memory_notes.py`
**依赖：** T6、T7
**步骤：**
1. `NoteUpdater(provider, project: NoteManager, user: NoteManager, notice: Callable[[str], None] | None = None)`；内部 `_busy` 标志。
2. `submit(messages) -> bool`：busy → False；否则取最近 12 条消息副本（`messages[-12:]`），起 daemon 线程跑 `_run`，返回 True。
3. `_run`：
   - `recent = list(...)`、`build_notes_request(...)` 组装；
   - `provider.stream([Message(role="user", text=请求)], [], SystemPrompt(stable=NOTES_SYSTEM_PROMPT, dynamic=""))` 收集全部 TextDelta 拼接；
   - `parse_notes_response` → 对每个 change：scope 选对应 NoteManager；action="add"/"update" → `write_note`（update 找不到目标时按 add 处理）；action="none" → 跳过；
   - 随后 `user.write_index(user_index)`、`project.write_index(project_index)`；
   - 成功：`notice(f"📝 记忆已更新：{新增数} 条新增 / {更新数} 条更新")`（notice 为 None 不调）；
   - 任何异常（ProviderError / NotesParseError / OSError）：吞掉，`notice("记忆更新失败，本轮跳过")`；
   - `finally: self._busy = False`。
4. 测试（FakeProvider 脚本化）：预设 JSON 响应 → 笔记文件与两份索引落盘正确（user/project 分开写）；成功 notice 内容正确；解析失败 → 无文件写入、失败 notice；submit 后 busy 期间再 submit 返回 False；更新在后台线程完成（轮询 `_busy` 或 join）。

**验证：** `python -m pytest tests/test_memory_notes.py -q` 全部通过。

---

## T9: SystemPromptBuilder 记忆块注入

**文件：** `mewcode/prompt/builder.py`、`tests/test_prompt.py`
**依赖：** 无（可并行）
**步骤：**
1. `SystemPromptBuilder.__init__` 增可选参数 `memory_loader: Callable[[], str] | None = None`。
2. `build()`：loader 非 None 时现取文本（空文本跳过），拼「`【记忆】\n<文本>`」块，dynamic 顺序为 supplement → 记忆块 → 自定义指令块。
3. 测试：loader 内容出现在 dynamic 且位于指令块前；loader 每次 build 都调用（用可变返回值断言即时可见）；loader 返回空 → 无【记忆】块；不传 loader 行为与原来一致（现有测试回归）。

**验证：** `python -m pytest tests/test_prompt.py tests/test_agent_loop.py -q` 全部通过。

---

## T10: Agent 透传与恢复压缩入口

**文件：** `mewcode/agent/loop.py`、`tests/test_memory_integration.py`
**依赖：** T9
**步骤：**
1. `Agent.__init__` 增可选参数 `memory_loader: Callable[[], str] | None = None`，透传给 `SystemPromptBuilder`。
2. 增 `maybe_compact_on_restore(self) -> str | None`：
   - `system = self._builder.build([], 1)`；
   - `self._compressor.needs_auto(self._session.messages, system)` 为 False → 返回 None；
   - 为 True → `summarize(self._session, manual=False)`，成功返回中文反馈（含压缩条数/保留条数/节省 token）；`SummarizeError` → 返回 `f"恢复后压缩失败: {e}"`（不抛出）。
3. 测试：空会话返回 None；小 context_window + 长历史（FakeProvider 脚本化摘要响应）→ 会话消息变少、返回非空反馈；压缩失败路径返回错误文本。

**验证：** `python -m pytest tests/test_memory_integration.py tests/test_agent_loop.py tests/test_compressor.py -q` 全部通过。

---

## T11: MemoryContext 装配

**文件：** `mewcode/memory/__init__.py`、`tests/test_memory_integration.py`
**依赖：** T4、T5、T6
**步骤：**
1. `MemoryContext` dataclass：`store`、`log`、`session`、`resume_summary`、`memory_loader`。
2. `build_memory_context(project_root: Path, user_dir: Path, resume: str | None) -> MemoryContext`：
   - `store = SessionStore(project_root / ".mewcode" / "sessions")`；
   - `cleaned = store.cleanup_expired(30)`，cleaned > 0 时记入 resume_summary（「已清理 N 个过期会话」）；
   - resume is None → `new_log()`、空 ChatSession；
   - resume == "" → `list_recent(1)`，无会话按新会话；有则取第一个 id 走恢复；
   - resume 指定 ID → 恢复；`FileNotFoundError` → resume_summary 加「会话 X 不存在，已按新会话继续」；
   - 恢复：`load(id)` 有效消息为 0 → resume_summary 加「会话 X 无有效记录，已按新会话继续」；否则 `session.restore(result.messages)`、`maybe_add_time_gap_notice(...)`、`SessionLog(path)` 继续追加、resume_summary 组装「已恢复会话 <id>（标题，N 条消息）」+ 坏行/截断/时间提醒提示；
   - `memory_loader`：lambda 现读 `user_dir/"memory"/"index.md"` 与 `project_root/".mewcode"/"memory"/"index.md"`（读失败按空）→ `build_memory_block`。
3. `__all__` 导出 MemoryContext / build_memory_context / SessionStore / SessionLog / load_instructions / NoteManager / NoteUpdater 等。
4. 测试：新会话路径（log 文件存在、session 为空、resume_summary 含清理提示）；指定 ID 恢复（消息恢复、7 天前存档插入提醒、resume_summary 文本）；无效 ID 与新会话降级；loader 读取索引内容正确拼接。

**验证：** `python -m pytest tests/test_memory_integration.py -q` 全部通过。

---

## T12: CLI 接入

**文件：** `mewcode/cli.py`
**依赖：** T2、T11
**步骤：**
1. `--resume` 参数：`nargs="?"`、`const=""`、`default=None`，help「恢复会话：缺省恢复最近一个，可指定会话 ID」。
2. 指令加载：`load_instructions(Path.cwd().resolve(), Path.home()/".mewcode")`，warnings 逐条打印到 stderr；与 `load_custom_instructions(args.config)` 非空拼接（三层指令在前、config 字段在后），传给 `run()` 的 custom_instructions。
3. `ctx = build_memory_context(...)`；`ctx.resume_summary` 非空打印（启动输出）。
4. `run(provider, config, registry, custom_instructions=指令文本, gate, memory=ctx)`。
5. 测试（test_memory_integration.py 增 CLI 装配测试）：临时目录 monkeypatch cwd + 三层指令文件 → `main(["--config", ...])` 捕获输出断言指令顺序与警告（或拆出纯函数便于测试——若 main 入口难测，把装配逻辑抽为 `_assemble(argv)` 纯函数后测试）。

**验证：** `python -m compileall mewcode/`；`python -m pytest tests/test_memory_integration.py -q` 通过；`python -m mewcode --help` 输出含 --resume。

---

## T13: /resume 命令登记

**文件：** `mewcode/tui/commands.py`
**依赖：** 无（可并行）
**步骤：**
1. `COMMAND_NAMES` 加 `"/resume"`。
2. `_HELP_TEXT` 加一行：`/resume   列出最近会话并恢复其中一个`。
3. `handle_command` 对 "/resume" 返回提示「请使用 /resume 交互选择（由界面处理）」——实际交互在 app.py（T14），此处仅为防御分支。

**验证：** `python -m compileall mewcode/tui/`；`python -m pytest tests/ -q -k "not memory"` 现有测试回归通过。

---

## T14: TUI 落盘、笔记触发与提示队列

**文件：** `mewcode/tui/app.py`
**依赖：** T12
**步骤：**
1. `run()` 增可选参数 `memory: MemoryContext | None = None`：
   - `session = memory.session if memory else ChatSession()`；
   - `self.log = memory.log`（None 时不落盘）；
   - 建 `NoteUpdater(provider, NoteManager(项目 memory 目录), NoteManager(用户 memory 目录), notice=notice_queue.put)`；notice_queue 为 `queue.Queue[str]`（memory None 时不建）。
2. `_agent_round` 增参数 `log`/`updater`：轮前记录 `len(session.messages)`；`RoundEnd` 拿到后把新增切片 `log.append_messages(新增, time.time())`；`end.reason is RoundEndReason.OK` 时 `updater.submit(session.messages)`。
3. 主循环：每次 `prompt()` 返回后先排空 notice_queue，逐条 `console.print(Text(msg, style=_TIMER_STYLE))`。
4. 保持无 memory 参数时行为与现状一致（回归测试保障）。

**验证：** `python -m compileall mewcode/tui/`；`python -m pytest tests/ -q` 全部通过。

---

## T15: /resume 交互与欢迎面板

**文件：** `mewcode/tui/app.py`
**依赖：** T14
**步骤：**
1. 欢迎面板（`_print_welcome`）增 `resume_summary` 参数：非空时加一行展示。
2. 主循环在 `handle_command` 之前拦截 `text == "/resume"`（memory 为 None 时提示不可用），走 `_resume_flow(console, memory, agent, gate)`：
   - `store.list_recent(5)` 为空 → 打印「没有可恢复的会话。」返回；
   - 面板列出编号/标题/ID/消息数/最后活动时间；
   - `prompt("选择编号（回车取消）> ")`：EOF/空输入取消；非数字或越界提示重输（同 _ask_user 循环模式）；
   - 选中 → `store.load(id)` → `session.restore(result.messages)` → `memory.log = SessionLog(路径)` → `maybe_add_time_gap_notice` → 打印恢复信息（标题/消息数/坏行/截断）→ `agent.maybe_compact_on_restore()` 非空则打印其反馈；
   - `FileNotFoundError` / 无有效记录 → 打印错误提示返回。
3. 恢复后 estimator 未锚定：无需处理（首次请求自动锚定，ch09 F2 已支持无锚点全量折算）。

**验证：** `python -m compileall mewcode/tui/`；`python -m pytest tests/ -q` 全部通过；tmux 手动场景归 checklist 阶段。

---

## T16: 全量回归

**文件：** 全部
**依赖：** T1–T15
**步骤：**
1. `python -m pytest tests/ -q` 全部通过（含既有全部测试）。
2. `python -m compileall mewcode/` 无错误。
3. 提交代码（ch11 开发提交，按任务分组提交）。

**验证：** 两条命令零失败。

---

## 执行顺序

```
T1 ──→ T2 ──→ T3 ──────────────→ T11 ──→ T12 ──→ T14 ──→ T15 ──→ T16
 │                              ↑
 └──→ T4 ──→ T5 ────────────────┘
T6 ──→ T8 ──┘（T8 依赖 T7；T6/T7 可与 T2/T4 并行）
T7 ──→ T8
T9 ──→ T10 ──┘（T10 与 T11 可并行）
T13 可与 T12 并行
```
