# ch11 记忆系统 Plan

## 架构概览

新增 `mewcode/memory/` 包承载全部记忆逻辑，与现有模块的耦合点收窄到四处：

- **cli.py**：`--resume` 参数、三层指令加载、记忆上下文装配（会话创建/恢复、过期清理）、把记忆上下文传给 TUI
- **tui/app.py**：欢迎面板展示恢复信息、`/resume` 交互、每轮结束后触发异步笔记更新与提示打印
- **prompt/builder.py**：新增可选的记忆索引加载器，每轮 build 时现读索引注入 dynamic（不破坏 stable 缓存）
- **agent/loop.py**：新增 `maybe_compact_on_restore()`——恢复后 token 超限先压一次（复用 ch09 Compressor）

恢复的会话直接复用现有 `ChatSession`/`Agent` 全套机制：加载结果灌进 ChatSession 后，压缩、估计、Plan Mode、权限全部照常工作，无需为恢复路径写第二套循环。

## 核心数据结构

### JSONL 行格式（会话存档）

每行一个 JSON 对象，`type` 三选一，`ts` 为 `time.time()` 浮点：

```json
{"type": "user", "text": "...", "summary": false, "ts": 1720000000.123}
{"type": "assistant", "text": "...", "thinking": "...", "thinking_raw": [...], "tool_uses": [{"id": "...", "name": "...", "args": {...}}], "summary": false, "ts": ...}
{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": false, "ts": ...}
```

- `summary` 标记照存（ch09 压缩产物的语义恢复后保留）
- `thinking_raw` 原样 JSON 序列化（含 signature），恢复后随消息原样回传
- 一个 assistant 消息的全部 `tool_uses` 写在一行；工具结果每条记录一行

### LoadResult（sessions.py）

```python
@dataclass
class LoadResult:
    messages: list[Message]      # 恢复出的消息（截断后的有效尾巴）
    skipped_lines: int           # 跳过的坏行数
    truncated: bool              # 是否发生过截断
    last_ts: float | None        # 最后一条消息的时间戳
    title: str                   # 首条用户消息截断 50 字符（现算）
```

### SessionSummary（sessions.py，/resume 列表用）

```python
@dataclass
class SessionSummary:
    id: str
    title: str
    message_count: int
    mtime: float
```

### SessionStore / SessionLog（sessions.py）

```python
class SessionStore:
    def __init__(self, directory: Path) -> None        # .mewcode/sessions
    def new_log(self) -> tuple[str, SessionLog]        # 生成 ID（YYYYMMDD-HHMMSS-xxxx，
                                                       # 4 位随机防同秒撞车，撞车重试）
    def load(self, session_id: str) -> LoadResult      # 恢复加载（坏行跳过+截断）
    def list_recent(self, n: int = 5) -> list[SessionSummary]  # 扫 JSONL 现算标题/计数
    def cleanup_expired(self, days: int = 30) -> int   # 按 mtime 删除，返回删除数

class SessionLog:
    def __init__(self, path: Path) -> None             # 追加模式打开
    def append_messages(self, messages: list[Message], ts: float) -> None
                                                       # 每条消息一行 JSON + flush
```

### 笔记变更（notes.py）

```python
@dataclass
class NoteChange:
    action: str      # "add" | "update" | "none"
    note_type: str   # user_preference | correction | project_knowledge | reference
    scope: str       # "user" | "project"
    name: str        # 短 slug（文件名与索引锚点）
    description: str # 一句话摘要（索引行用）
    content: str     # 笔记全文
```

### NoteManager / NoteUpdater（notes.py）

```python
class NoteManager:
    """一级记忆目录（.mewcode/memory 或 ~/.mewcode/memory）的读写。"""
    def __init__(self, directory: Path) -> None
    def index_text(self) -> str                       # index.md 全文，缺失返回 ""
    def load_notes(self) -> str                       # 全部笔记拼接（带文件名分隔）
    def write_note(self, change: NoteChange) -> None  # 写 <name>.md（frontmatter 含
                                                      # name/description/type/updated）
    def write_index(self, text: str) -> None          # 写 index.md；超 200 行/25KB 截断

class NoteUpdater:
    """轮结束后异步笔记更新：单后台线程 + busy 跳过。"""
    def __init__(self, provider, project: NoteManager, user: NoteManager,
                 notice: Callable[[str], None] | None = None) -> None
    def submit(self, messages: list[Message]) -> bool  # 取最近 12 条消息副本，空闲时
                                                       # 启动后台线程；busy 返回 False
```

### 记忆上下文（memory/__init__.py）

```python
@dataclass
class MemoryContext:
    store: SessionStore                    # 存档目录（/resume 与落盘共用）
    log: SessionLog                        # 当前会话日志（新建或恢复目标）
    session: ChatSession                   # 已恢复或为空的会话
    resume_summary: str                    # 恢复信息（欢迎面板展示；新会话为空串）
    memory_loader: Callable[[], str]       # 每轮读两份 index.md 拼文本

def build_memory_context(project_root: Path, user_root: Path,
                         resume: str | None) -> MemoryContext
    # resume: None=新会话；""=最近会话；"<ID>"=指定会话
```

### 既有类型的小扩展

- `ChatSession.restore(messages: list[Message]) -> None`：整体替换消息列表（/resume 用）
- `SystemPromptBuilder(env, custom_instructions, memory_loader=None)`：build 时现取索引文本，拼成 `【记忆】` 块进 dynamic（顺序：supplement → 记忆块 → 自定义指令块）
- `Agent.maybe_compact_on_restore() -> str | None`：`needs_auto` 触发 → `summarize(manual=False)`，返回中文反馈；无需压缩返回 None

## 模块设计

### memory/instructions.py — 三层指令加载

**职责：** 按优先级加载并展开三层指令文件；@include 递归展开。

**对外接口：**

```python
@dataclass
class InstructionResult:
    text: str            # 按优先级拼接后的完整指令文本
    warnings: list[str]  # 中文警告（缺失层不警告；损坏/include 问题警告）

def load_instructions(project_root: Path, user_dir: Path) -> InstructionResult
```

**内部规则：**
- 加载顺序（高优先级在前，空行分隔）：项目根 `MEWCODE.md` → 项目 `.mewcode/instructions.md` → `~/.mewcode/instructions.md`；任一层缺失静默跳过
- 行首 `@include <路径>`（路径可带引号）：相对路径按当前文件所在目录解析
  - 嵌套深度 ≤3（顶层为第 1 层，第 4 层展开被跳过并警告）
  - 全局 visited 集合：同一文件只加载一次，重复引用静默跳过（防环）
  - 越界拦截：项目级文件的 include 目标必须在 project_root 内；用户级文件的目标必须在 user_dir 内；越界跳过并警告
  - 目标缺失/读取失败：跳过并警告
- 文件读取失败：警告、该层按空处理，不抛异常（N2）

**依赖：** 无（纯标准库）。

### memory/sessions.py — 会话存档与恢复

**职责：** 会话 ID 生成、JSONL 追加写、恢复加载（坏行跳过/截断/现算元信息）、过期清理、时间跨度提醒。

**对外接口：** SessionStore / SessionLog / LoadResult / SessionSummary（见上）+ 

```python
def maybe_add_time_gap_notice(messages: list[Message], last_ts: float | None,
                              now: float, days: int = 7) -> bool
    # 距今超 days 天：追加一条 summary=True 的提醒消息，返回 True
```

**内部规则：**
- 行反序列化：JSON 失败或 type/关键字段缺失 → 坏行，计数跳过
- 截断判定：读到带 `tool_uses` 的 assistant 行后，向后收集 `tool_result` 行直到全部 id 配平；若先遇到 EOF 或非 tool_result 行仍未配平 → 丢弃该 assistant 行及其后所有行（`truncated=True`）
- 末尾孤立的 user 文本行（未获回复）保留——恢复后模型会直接回答它
- 标题现算：第一行有效 user 文本截断 50 字符（超长加省略号）；消息数 = 有效行数

**依赖：** `mewcode.session.Message`（序列化方向），无第三方库。

### memory/notes.py — 自动笔记

**职责：** 笔记/索引文件读写、异步 LLM 更新（四分类、去重、归属判断、索引精简）、记忆块拼接。

**对外接口：** NoteManager / NoteUpdater / NoteChange（见上）+

```python
def build_memory_block(user_index: str, project_index: str) -> str
    # 拼成「## 用户级记忆 / ## 项目级记忆」两小节；都为空返回 ""
```

**内部规则：**
- 笔记文件格式：frontmatter（name/description/type/updated）+ 正文；文件名为 `<name>.md`
- 索引文件 `index.md`：每行 `- [name](name.md) — description`
- LLM 输入：最近 12 条消息（原文）+ 用户/项目两份索引全文 + 全部笔记全文（每条截断 1500 字符）
- LLM 输出：JSON（changes 列表 + 新索引全文）；解析失败 → 本轮静默放弃
- action=none 的 change 不写文件；update 找不到目标笔记时按 add 处理
- 索引硬性兜底：写盘前校验行数与字节数，超限截断（NoticeManager 警告提示）
- 线程模型：单后台 daemon 线程 + busy 标志，提交时 busy 则跳过本轮（N4 每轮最多一次）
- provider 调用：同步 `provider.stream()` 在后台线程直接跑（httpx.Client 每次调用新建、无共享状态，与主循环并发安全）
- 全部异常吞掉：失败/解析错均静默，通过 notice 回调至多发一条灰色提示

**依赖：** provider（LLM 调用）、memory/prompts.py（模板与解析）、`mewcode.session.Message`。

### memory/prompts.py — 笔记更新的 LLM 提示

**对外接口：**

```python
NOTES_SYSTEM_PROMPT: str  # 固定系统提示：四分类定义、去重规则、输出 JSON schema、
                          # 索引 ≤200 行约束说明
def build_notes_request(recent: list[Message], user_index: str, project_index: str,
                        user_notes: str, project_notes: str) -> str  # 拼 user 消息
def parse_notes_response(text: str) -> tuple[list[NoteChange], str]
    # 返回 (changes, 新索引全文)；解析失败抛 NotesParseError
```

**依赖：** 无。

### 既有模块改动

| 文件 | 改动 |
|------|------|
| `mewcode/cli.py` | `--resume [ID]` 参数（缺省恢复最近会话）；三层指令加载（`load_instructions` + config 字段拼接）；`build_memory_context`；过期清理提示；传给 `run()` |
| `mewcode/session.py` | 增 `ChatSession.restore(messages)` |
| `mewcode/prompt/builder.py` | `SystemPromptBuilder` 增可选 `memory_loader`；build 时拼【记忆】块 |
| `mewcode/agent/loop.py` | Agent 构造增可选 `memory_loader` 透传给 builder；增 `maybe_compact_on_restore()` |
| `mewcode/tui/app.py` | `run()` 接收 MemoryContext；欢迎面板含恢复信息；`/resume` 交互；每轮结束后落盘新增消息 + 触发笔记更新；提示队列打印 |
| `mewcode/tui/commands.py` | COMMAND_NAMES/帮助文本加 `/resume`（实际交互在 app.py） |

## 模块交互

### 启动（新会话）

```
cli: load_config → load_instructions(三层) + custom_instructions 拼接
   → build_memory_context(resume=None)：SessionStore → cleanup_expired(30) 打印 → new_log
   → run(provider, config, registry, 指令文本, gate, memory=ctx)
app: 建 Agent(memory_loader=ctx.memory_loader) → 欢迎面板（新会话）
```

### 启动（--resume [ID]）

```
cli: build_memory_context(resume=ID)：
   → SessionStore.load(ID)：坏行跳过 + 截断 + 时间跨度提醒（>7 天插入提醒消息）
   → SessionLog(原文件) 继续追加；resume_summary 含 ID/标题/消息数/跳过与截断提示
   → 文件无有效记录：报错，按新会话继续
app: 建 Agent → agent.maybe_compact_on_restore()（超限压一次并提示）→ 欢迎面板（恢复信息）
```

### /resume（运行中）

```
app: store.list_recent(5) → 面板列出编号/标题/ID/消息数/时间 → 输入编号
   → store.load(id) → session.restore(messages) → 切换 self.log 为对应 SessionLog
   → agent.maybe_compact_on_restore() → 打印恢复信息
```

### 每轮 Agent Loop 结束

```
app._agent_round 拿到 RoundEnd：
 1. 落盘：session.messages[轮前长度:] → log.append_messages（ERROR 回滚后自然只含完整轮次）
 2. end.reason is OK → note_updater.submit(session.messages)（后台线程，busy 跳过）
app 主循环：prompt() 返回后先排空提示队列打印灰色提示（「📝 记忆已更新…」等）
```

### 每轮请求构建（记忆注入）

```
Agent.run 每轮: self._builder.build(directives, iteration)
  → memory_loader() 现读两份 index.md → 【记忆】块进 dynamic
  → 本轮笔记更新完成后，下一轮 build 自动可见
```

## 文件组织

```
mewcode/memory/
├── __init__.py        — 包导出、MemoryContext、build_memory_context（装配）
├── instructions.py    — load_instructions、InstructionResult、@include 展开
├── sessions.py        — SessionStore、SessionLog、LoadResult、SessionSummary、
│                        load 恢复逻辑、maybe_add_time_gap_notice
├── notes.py           — NoteManager、NoteUpdater、NoteChange、build_memory_block
└── prompts.py         — NOTES_SYSTEM_PROMPT、build_notes_request、parse_notes_response

tests/
├── test_memory_instructions.py  — 三层加载/优先级/@include 深度、环、越界
├── test_memory_sessions.py      — ID/追加/坏行/截断/扫描/清理/时间提醒
├── test_memory_notes.py         — NoteManager 读写与截断、NoteUpdater（FakeProvider）、
│                                  解析失败静默、busy 跳过
└── test_memory_integration.py   — build_memory_context、builder 记忆块注入、
                                   ChatSession.restore、maybe_compact_on_restore
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 存档格式 | JSONL 追加写，无 meta 文件 | 用户指定：追加快、崩溃只丢最后一行、坏行可跳过；ID/标题/消息数扫 JSONL 现算，少一份要同步的状态 |
| 会话 ID | `YYYYMMDD-HHMMSS-xxxx`（4 位随机） | 用户指定；可读、按时间排序即按 ID 排序；撞车重试兜底 |
| 截断语义 | 未配平 tool_use 的 assistant 行及其后全部丢弃 | 崩溃尾巴模型从未见过结果，保留会产生幻觉续写；末尾孤立 user 行保留（恢复后模型直接回答） |
| 时间提醒消息 | 复用 summary=True 标记的 user 消息 | ch09 已有语义：压缩时不当用户原文保留，二次压缩不误伤 |
| 恢复后压缩 | 复用 ch09 Compressor：needs_auto 判断 + summarize(自动模式) | 用户指定「先压一次」；整套阈值/熔断/保留区逻辑已验收，不重写第二套 |
| 记忆注入位置 | dynamic 部分，每轮 build 现读索引文件 | 索引变化不打断 stable 缓存前缀；「本轮新笔记下一轮可见」自然成立 |
| 笔记更新时机 | 轮结束（TUI 层）→ 单后台线程 busy 跳过 | spec 指定轮自然结束后异步；busy 跳过保证每轮最多一次、请求不堆积 |
| provider 并发 | 更新线程直接调用同一 provider 实例 | httpx.Client 每次调用新建、无共享可变状态，并发安全；避免再造 provider |
| 去重与归属 | 一阶段 LLM 判定（对话尾巴+索引+笔记全文 → changes+新索引） | 用户指定去重交给 LLM；一阶段省一次往返，笔记总量被索引规模约束 |
| /resume 交互位置 | app.py 内多步交互，commands.py 只登记命令名 | 恢复要换 session/log 并触发压缩，句柄闭包传参不如就地处理清晰 |
| 指令注入兼容 | 三层指令 + config.custom_instructions 拼接后仍走【自定义指令】块 | 不动 SystemPrompt 结构与缓存布局，ch01 以来的行为保持 |
| 越界 include 边界 | 项目级→项目根内、用户级→~/.mewcode/ 内 | 用户指定拦截跳出项目目录；用户级文件给 home 范围对称处理 |
| 新依赖 | 零 | 全部标准库（json/os/time/random/threading/pathlib） |
