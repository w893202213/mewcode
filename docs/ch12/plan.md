# ch12 斜杠命令系统 Plan

## 架构概览

新增独立包 `mewcode/commands/`——纯逻辑、不 import Rich / prompt_toolkit（满足 N3/N4），五个模块：

- **registry.py**：`CommandSpec` 元数据 + `CommandRegistry` 登记/别名解析/冲突校验
- **parser.py**：斜杠文本 → `ParsedCommand`（命令名小写 + 参数原文）
- **ui.py**：`UiController` Protocol（界面控制接口）+ 查询结果数据类
- **builtins.py**：12 条内置命令的元数据与处理函数、`/help` 动态渲染、`build_registry()`
- **dispatch.py**：`CommandResult`（CONTINUE/EXIT）+ `dispatch()` 分发器

TUI 层三个改动点：

- 新建 `tui/uicontroller.py`：`TuiUiController` 实现 `UiController`，聚合 console / session / agent / gate / memory / config，并承担 token 累计
- 新建 `tui/completion.py`：`CommandCompleter`，前缀匹配可见命令与别名，单匹配直接补主名、多匹配弹菜单
- 改造 `tui/app.py`：回车入口分流器（命令走分发、其余送 Agent）、状态栏（bottom_toolbar 直读模式状态）、删除 `/resume` 主循环特殊拦截与 `_resume_flow`（迁入 TuiUiController）

`tui/commands.py` 删除（命令逻辑全部收敛到 `commands/` 包）。`cli.py` 在进入 TUI 前调用 `build_registry()`，冲突抛 `CommandConflictError` → stderr 输出详情 + `sys.exit(1)`（启动阶段 panic，不进入 TUI）。

## 核心数据结构

### `CommandType`（registry.py）

```python
class CommandType(Enum):
    LOCAL = auto()   # 纯本地：只读计算与展示
    UI = auto()      # 影响界面状态：改会话/模式/退出
    PROMPT = auto()  # 提示词：预设文本送进对话交给 AI
```

### `CommandSpec`（registry.py）

```python
@dataclass(frozen=True)
class CommandSpec:
    name: str                                # 主命令名（注册时归一为小写）
    description: str                         # 简短描述（/help 与补全菜单用）
    usage: str = ""                          # 用法示例，如 "/permission [show]"
    aliases: tuple[str, ...] = ()            # 别名（不含斜杠，注册时归一为小写）
    arg_hint: str = ""                       # 参数提示（仅展示用，不做校验）
    type: CommandType = CommandType.LOCAL    # 执行模式分类
    hidden: bool = False                     # 隐藏命令不参与补全与 /help
    handler: Callable[[ParsedCommand, UiController], CommandResult] | None = None
```

### `CommandRegistry`（registry.py）

```python
class CommandRegistry:
    def register(self, spec: CommandSpec) -> None   # 登记元数据；name/aliases 归一为小写
    def validate(self) -> list[str]                 # 冲突与合法性问题列表，空列表=通过
    def resolve(self, name: str) -> CommandSpec | None   # 含别名解析；未命中返回 None
    def visible(self) -> list[CommandSpec]          # 非隐藏命令，按名称排序（补全/帮助数据源）
    def all(self) -> list[CommandSpec]              # 全部命令（诊断用）

class CommandConflictError(Exception):
    # 携带冲突详情文本；build_registry 校验失败时抛出，cli 捕获后 stderr + exit(1)
```

校验规则（validate）：命令名为空/含空白；命令名撞另一条命令名；命令名撞任一别名；别名撞另一命令名/别名。存储用有序列表，索引在 validate 通过后构建，杜绝 dict 覆盖静默吞重名。

### `ParsedCommand`（parser.py）

```python
@dataclass(frozen=True)
class ParsedCommand:
    name: str   # 小写命令名（不含 /），可能为空串（如输入 "/" 或 "/  x"）
    args: str   # 命令名之后的原文（去掉分隔命令名与参数的第一个空白），可为空串
```

### `UiController`（ui.py）

```python
class UiController(Protocol):
    # 展示
    def display(self, text: str) -> None
    # 提示词类：注入对话并触发 Agent 一轮
    def send_user_message(self, text: str) -> None
    # 模式切换 + 状态栏刷新
    def enter_plan_mode(self) -> None
    def exit_plan_mode(self) -> None
    def cycle_permission_mode(self) -> str        # 返回新档位中文标签
    def refresh_status(self) -> None
    # 会话生命周期
    def clear_conversation(self) -> None
    def compact_now(self) -> str                  # 返回压缩反馈文本
    # 多步交互（/resume 用）
    def choose(self, title: str, items: list[str]) -> int | None   # 1 起编号；None=取消
    # 状态查询
    def status_info(self) -> StatusInfo
    def session_info(self) -> SessionInfo
    def memory_info(self) -> MemoryInfo
    def permission_info(self) -> PermissionInfo
    def list_sessions(self) -> list[ResumeItem]
    def restore_session(self, session_id: str) -> str   # 返回汇总反馈文本
```

查询结果数据类（ui.py，均为 dataclass）：

```python
@dataclass
class TokenStats:
    round_in: int = 0                       # 最近一轮输入
    round_out: int = 0                      # 最近一轮输出
    round_cache_hit: int | None = None      # 服务端未返回时 None
    round_cache_write: int | None = None
    total_in: int = 0                       # 会话累计
    total_out: int = 0
    message_count: int = 0
    context_tokens: int | None = None       # 上下文占用估算（字符折算，展示级）
    window: int = 0                         # 上下文窗口上限

@dataclass
class StatusInfo:
    config_name: str
    model: str
    thinking: bool
    permission_label: str
    plan_mode: bool
    tokens: TokenStats

@dataclass
class SessionInfo:
    enabled: bool          # 记忆系统是否启用
    session_id: str = ""   # 未启用时为空
    title: str = ""
    message_count: int = 0
    archive_path: str = ""
    last_activity: str = ""

@dataclass
class MemoryInfo:
    enabled: bool
    instruction_files: list[str] = []   # 三层指令文件存在状态描述行
    project_notes: int = 0
    user_notes: int = 0
    session_files: int = 0

@dataclass
class PermissionInfo:
    mode_label: str
    plan_mode: bool
    session_rules: int = 0
    permanent_rules: int = 0

@dataclass
class ResumeItem:
    id: str
    title: str
    message_count: int
    label: str   # 列表展示文本（标题 + ID + 消息数 + 时间）
```

## 模块设计

### commands/parser.py

**职责**：把一行输入判定为「命令 / 空白 / 普通消息」。

```python
def parse_command(text: str) -> ParsedCommand | None:
    # 空或纯空白 → None（调用方 continue，不发 Agent）
    # 不以 / 开头 → None（普通消息，调用方送 Agent）
    # 匹配 ^/(\S*)(?:[ \t]+(.*))?$ → ParsedCommand(小写名, args)
    # args = 命令名之后的原文，去掉首个分隔空白，其余原样保留（"/help  abc" → args=" abc"）
```

**依赖**：无。**测试**：纯函数，无 TUI 依赖。

### commands/registry.py

**职责**：元数据登记、别名索引、冲突校验、可见列表。

**依赖**：commands/ui.py（类型标注）。**测试**：冲突矩阵（同名/别名撞名/别名撞别名/非法名）、resolve 别名命中、visible 排除 hidden。

### commands/ui.py

**职责**：界面控制接口协议与查询数据类。不 import 渲染框架，处理函数唯一依赖。

**依赖**：无（协议 + dataclass）。

### commands/dispatch.py

**职责**：统一分发。

```python
class CommandResult(Enum):
    CONTINUE = auto()   # 继续输入循环
    EXIT = auto()       # 退出 TUI

def dispatch(registry, parsed, ui) -> CommandResult:
    # resolve 未命中或无 handler → ui.display("未知命令: /xxx（输入 /help 查看可用命令）")，CONTINUE
    # 命中 → handler(parsed, ui)；handler 返回 None 按 CONTINUE
```

**依赖**：registry、ui。

### commands/builtins.py

**职责**：12 条内置命令的元数据与处理函数；`/help` 动态渲染；`build_registry()`。

处理函数签名统一 `(cmd: ParsedCommand, ui: UiController) -> CommandResult | None`，内部只做「参数判断 + 调用 ui + 纯文本格式化」，格式化函数可独立单测。别名表：

| 命令 | 别名 | 命令 | 别名 |
|------|------|------|------|
| /help | h | /memory | m |
| /exit | q、quit | /permission | mode、perm |
| /review | r | /session | s |
| /compact /clear /plan /do /status /resume | 无 | | |

各命令实现要点：

- **/help**：`args` 空 → 列出 `registry.visible()`（名称+描述+别名，含「输入 /help <命令> 查详情」）；`args` 非空 → resolve 单条详情（描述/用法/别名/类型），未命中提示未知命令。registry 经闭包捕获（`register_builtins` 传入后注册）。
- **/clear**：`ui.clear_conversation()` + 提示「已清空对话上下文，重新开始。」
- **/plan**：`ui.status_info().plan_mode` 已开 → 提示已在计划模式；否则 `ui.enter_plan_mode()` + 进入提示。
- **/do**：对称：`ui.exit_plan_mode()` + 切回提示；不在计划模式时提示。
- **/compact**：`text = ui.compact_now()`，`ui.display(text)`。
- **/permission**：`args.strip() == "show"` → 格式化 `ui.permission_info()`（档位+计划模式+会话规则数+永久规则数）；否则 `label = ui.cycle_permission_mode()` + 显示新档位。`/mode` 别名零差异。
- **/status**：格式化 `ui.status_info()`：配置名、模型、思考模式、权限档、计划模式、本轮 token（输入/输出/缓存，服务端返回才显示）、累计 token、上下文占用（消息数 + 估算 token 对窗口上限）。
- **/session**：格式化 `ui.session_info()`；`enabled=False` 时显示「纯内存会话（无存档）」。
- **/memory**：格式化 `ui.memory_info()`：三层指令文件状态、项目/用户笔记数、会话存档数；`enabled=False` 时提示记忆系统未启用。
- **/review**：`ui.send_user_message(_REVIEW_PROMPT)`，返回 CONTINUE。`_REVIEW_PROMPT` 常量：「请审查当前项目的改动：用 run_command 执行 git diff 查看未提交改动（若工作区干净则查看最近一次提交），指出其中的 bug、风险与可改进点，并给出修改建议。」
- **/exit**：`ui.display("再见！👋")`，返回 EXIT。
- **/resume**：`ui.memory_info().enabled=False` → 提示记忆系统未启用；否则 `items = ui.list_sessions()`，空 → 提示没有可恢复会话；`idx = ui.choose("最近会话", [i.label for i in items])`，None → 取消；`feedback = ui.restore_session(items[idx-1].id)`，`ui.display(feedback)`。

```python
def build_registry() -> CommandRegistry:
    # 登记 12 条 → validate() 非空 → raise CommandConflictError(详情)
```

**依赖**：registry、parser（类型）、ui、dispatch（CommandResult）。**测试**：12 条 handler 用 FakeUiController 单测（各命令的输出文案与接口调用序列），/help 渲染含新注册命令。

### tui/uicontroller.py

**职责**：`UiController` 的 TUI 实现（facade）。构造参数：console、session、agent、gate、memory、config、root、`run_agent_round: Callable[[str], None]`（app.py 的 `_agent_round` 绑定）、`invalidate: Callable[[], None]`（PromptSession 重绘）。

- `display` → `console.print(Text(text))`（与现有 feedback 打印一致）
- `send_user_message` → `run_agent_round(text)`（完整渲染管线，与普通对话一致）
- `enter/exit_plan_mode` → `agent.enter_plan_mode()/exit_plan_mode()` + `refresh_status()`
- `cycle_permission_mode` → `agent.cycle_permission_mode()` 映射 `MODE_LABELS` 返回 + `refresh_status()`
- `refresh_status` → `invalidate()`（状态栏直读 live 状态，另有 0.5s 重绘兜底）
- `clear_conversation` → `session.clear()`
- `compact_now` → `agent.compact_now()`
- `choose` → 面板展示 items + `prompt("选择编号（回车取消）> ")` 循环校验（现有 `_resume_flow` 交互迁入）
- `list_sessions` → `memory.store.list_recent(5)` 转 `ResumeItem`
- `restore_session` → 现有 `_resume_flow` 恢复主体迁入（load、restore、时间跨度提醒、log 换句柄、`maybe_compact_on_restore`），返回汇总反馈文本
- `status_info` → config 字段 + 档位标签 + plan_mode + `TokenStats`（轮数据来自 `record_usage` 记录，`context_tokens` = 消息总字符 // 4 字符折算，`window` = config.context_window）
- `session_info` → `memory.log.path.stem` 作 ID、首条用户消息截断作标题、`len(session.messages)`、存档路径、文件 mtime 格式化；`memory is None` 时 `enabled=False`
- `memory_info` → 三层指令文件存在性（root/MEWCODE.md、root/.mewcode/instructions.md、~/.mewcode/instructions.md）、笔记目录 `*.md` 计数、存档目录 `*.jsonl` 计数
- `permission_info` → `MODE_LABELS[gate.mode]`、`agent.plan_mode`、`gate 规则统计`（经 RuleSet 新增 `counts()`）
- `record_usage(usage: dict)`（非接口方法，TUI 内部用）：记录最近一轮 + 累计，`_agent_round` 轮末调用

**依赖**：commands/ui.py、既有模块。**测试**：FakeUiController 已覆盖接口语义；TUI 实现的格式/映射函数尽量提为纯函数单测。

### tui/completion.py

**职责**：`CommandCompleter(Completer)`。仅当输入以 `/` 开头且光标前无空格时补全；前缀（小写）匹配 `registry.visible()` 的命令名或任一别名；0 匹配不弹；1 匹配 `Completion("/" + 主名, start_position=-len(输入))` 直接补主名；多匹配逐项 yield（`display_meta` 显示描述），重复主名去重（别名与主名同时命中只出现一次）。

**依赖**：registry。**测试**：匹配矩阵（单匹配/多匹配/别名命中补主名/隐藏不参与/参数位置不补全）。

### tui/app.py 改造

1. `run(..., registry: CommandRegistry)` 新参数；装配 `TuiUiController`（含 `run_agent_round`/`invalidate` 注入）。
2. `completer=CommandCompleter(registry)` 替换 `NestedCompleter`。
3. `bottom_toolbar` 改状态栏：`tracker.hint_active()` 时显示 Ctrl+C 提示（现状），否则显示 `[STRICT]`/`[DEFAULT]`/`[ACCEPT]`（gate.mode.value 大写）+ 计划模式追加 `[PLAN]`。
4. 主循环分流：
   ```python
   parsed = parse_command(text)
   if parsed is None:
       if not text.strip():
           continue            # 空白早返回
       _agent_round(...)       # 普通消息送 Agent
       continue
   if dispatch(registry, parsed, ui) is CommandResult.EXIT:
       return
   ```
5. `_agent_round` 轮末（正常与取消路径都）调用 `ui.record_usage(usage)`。
6. 删除 `_resume_flow` 与 `/resume` 特殊拦截（迁入 TuiUiController）；删除 `tui/commands.py` 及相关 import。

### permissions/rules.py 修改

`RuleSet` 增加只读统计 `counts() -> tuple[int, int]`：返回（会话规则数 = `len(_rules) - len(_file_rules)`，永久规则数 = `len(_file_rules)`）。

### cli.py 修改

进入 TUI 前：

```python
try:
    registry = build_registry()
except CommandConflictError as e:
    print(f"命令注册冲突：\n{e}", file=sys.stderr)
    sys.exit(1)
```

`run(..., registry=registry)`。

## 模块交互

**启动链**：cli.main → `build_registry()`（登记 12 条 → validate → 冲突即 stderr + exit(1)）→ `run(registry)` → 装配 TuiUiController + CommandCompleter + 状态栏 → 输入循环。

**输入链（回车入口分流器）**：prompt 返回 → `parse_command`：
- `None` 且空白 → continue
- `None` 且非斜杠 → `_agent_round`（Agent 路径，现状不变）
- `ParsedCommand` → `dispatch` → resolve（含别名）→ handler `(args, ui)` → 经 `ui.*` 完成全部界面副作用 → CONTINUE 回循环 / EXIT 退出

**/review 链（提示词类）**：handler → `ui.send_user_message(_REVIEW_PROMPT)` → `_agent_round` → Agent 流式渲染、权限询问、落盘，与普通对话完全一致。

**/resume 链（多步交互）**：handler → `ui.list_sessions()` → `ui.choose()`（面板+编号输入）→ `ui.restore_session(id)` → 反馈文本 `ui.display`。

**补全链**：Tab → CommandCompleter → `registry.visible()` 前缀匹配 → 单补主名 / 菜单。

**状态栏链**：bottom_toolbar 每次重绘直读 `gate.mode` + `agent.plan_mode`（0.5s 定时重绘兜底）；`/plan`、`/do`、`/permission` 经 `refresh_status → invalidate` 立即重绘。

**轮末链**：`_agent_round` 结束 → `ui.record_usage(usage)` → `/status` 的最近一轮与累计数据源。

## 文件组织

```
mewcode/
├── cli.py                      — 修改：启动时 build_registry + 冲突退出
├── commands/
│   ├── __init__.py             — 包导出
│   ├── registry.py             — CommandType / CommandSpec / CommandRegistry / CommandConflictError
│   ├── parser.py               — ParsedCommand / parse_command
│   ├── ui.py                   — UiController Protocol + TokenStats/StatusInfo/SessionInfo/MemoryInfo/PermissionInfo/ResumeItem
│   ├── dispatch.py             — CommandResult / dispatch
│   └── builtins.py             — 12 条内置命令、/help 渲染、build_registry
├── permissions/rules.py        — 修改：RuleSet 增 counts()
└── tui/
    ├── app.py                  — 修改：分流器 / 状态栏 / 接入 / 删 _resume_flow
    ├── uicontroller.py         — 新建：TuiUiController
    ├── completion.py           — 新建：CommandCompleter
    └── commands.py             — 删除（迁入 commands/ 包）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 命令逻辑放独立包 `commands/`，不 import 渲染框架 | 新建包 | 满足 N3 解耦与 N4 可测：注册/解析/处理函数全部脱离 TUI 单测 |
| 处理函数签名 `(ParsedCommand, UiController) -> CommandResult` | 统一签名 | 分发器零分支；命令实现唯一外部依赖是接口 |
| UiController 是聚合 facade（16 方法含会话生命周期与查询） | 单接口 | spec F4「只依赖一个界面控制接口」；TUI 实现内部聚合既有对象，职责清晰 |
| 别名解析在 `registry.resolve` | 名字表 + 别名表 | F6：handler 不感知调用名，别名与原命令完全等价 |
| 补全统一补主命令名 | 别名只参与匹配 | AC7 行为确定；避免别名在输入行里存活 |
| /resume 经 list_sessions/choose/restore_session 走统一分发 | 接口承载多步交互 | F5：主循环无特殊拦截，所有命令同一路径 |
| 状态栏 = bottom_toolbar 直读 live 状态 + invalidate | 不引入状态推送机制 | 0.5s 重绘兜底，切换模式即刷；渲染架构零改动 |
| 冲突检测在启动期：build_registry → validate → 抛异常 → cli stderr + exit(1) | 启动 panic | 「撞名直接退出，不等运行时再炸」；内置命令固定，正常启动恒通过 |
| token 累计在 TuiUiController.record_usage（轮末） | 展示级累计 | /status 数据源；上下文占用用字符折算估算（//4），不侵入 Agent 内部估算器 |
| 删除 tui/commands.py | 迁移 | 命令逻辑单一归属 commands/ 包，消除双份维护 |

## spec.md 修正清单（需确认）

1. **状态栏标记语言**：spec F4 写「英文标记」但示例用了中文（`[默认]`），与最初想法 `[DEFAULT]/[PLAN]` 矛盾。统一为英文枚举值：`[STRICT]` / `[DEFAULT]` / `[ACCEPT]` + 计划模式追加 `[PLAN]`；AC5 示例同步改。
2. **AC7 示例与 F6 冲突**：`/m` 同时命中 `/memory` 与别名 `/mode`（→/permission），按 F6 应为菜单而非单补。修正：`/cl` Tab → `/clear`；`/c` Tab → 菜单（/clear、/compact）；`/mo` Tab → 补 `/permission`（别名解析到主名）。
3. **AC2 参数语义明确**：`/help␣␣abc` → args = `" abc"`（命令名之后的原文，去掉首个分隔空白，其余多余空格原样保留）。
