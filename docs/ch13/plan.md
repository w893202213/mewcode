# ch13 技能系统 Plan

## 架构概览

新增 `mewcode/skills/` 包（纯逻辑，不依赖渲染框架，N3），四个模块 + 三个内置样板技能文件：

| 模块 | 职责 |
|------|------|
| `skills/model.py` | 技能数据模型：`Skill`/`SkillMode`、frontmatter 解析校验、`$ARGS` 参数替换 |
| `skills/discover.py` | 三级扫描、优先级覆盖、警告收集；白名单启动校验 |
| `skills/manager.py` | `SkillManager`：激活状态、钉住块快照、白名单并集、斜杠命令注册/撤销、独立执行登记 |
| `skills/use_skill.py` | `UseSkillTool`：系统级加载工具 |

改动点：`prompt/builder.py`（stable 追加【技能】块、dynamic 追加【已激活技能】块）、`agent/loop.py`（白名单收窄 + 执行侧拦截）、`commands/registry.py`（增 `unregister`）、`commands/builtins.py`（删 `/review`）、`commands/ui.py`（UiController 增 `run_skill`）、`cli.py`（发现→校验→装配）、`tui/app.py`（独立执行 runner + pending 触发）、`tui/uicontroller.py`（`run_skill` 实现）。

## 核心数据结构

```python
class SkillMode(Enum):
    SHARED = "shared"
    INDEPENDENT = "independent"

@dataclass(frozen=True)
class Skill:
    name: str                        # 唯一名字（校验：非空、不含空白）
    description: str                 # 一句话说明
    body: str                        # SOP 正文（未替换）
    tools: tuple[str, ...] | None    # 工具白名单；None = 不收窄
    mode: SkillMode                  # 缺省 SHARED
    history: int                     # 独立模式带入主对话消息条数；缺省 0
    model: str | None                # 独立模式指定模型
    dir: Path | None                 # 目录型技能的目录路径；单文件为 None
    source: Path                     # 文件路径（热更新重读用）
    level: str                       # 来源层级 project/user/builtin（报错展示用）

@dataclass
class DiscoverResult:
    skills: dict[str, Skill]    # 生效集（同名已按优先级覆盖）
    warnings: list[str]

@dataclass
class ActivationResult:
    ok: bool
    error: str = ""            # 失败原因（未知技能/重读失败）
    skill: Skill | None = None
    body: str = ""             # 参数替换后的 SOP
    first_time: bool = False   # 是否首次激活（命令注册去重用）

def parse_skill_file(path: Path) -> tuple[Skill | None, str | None]
    """解析单个技能文件；失败返回 (None, 原因)。"""

def substitute_args(body: str, args: str) -> str
    """替换 $ARGS / $ARGUMENTS 占位符。"""

class SkillManager:
    def __init__(self, skills: dict[str, Skill]): ...
    def activate(self, name: str, args: str = "") -> ActivationResult  # 每次从磁盘重读（热更新）
    def active_snapshot(self) -> str        # 【已激活技能】块，每轮 build 现取
    def whitelist_active(self) -> bool      # 是否有白名单收窄生效
    def whitelist(self) -> frozenset[str]   # 激活技能白名单并集
    def is_allowed(self, tool_name: str) -> bool
    def clear(self) -> None                 # 清激活 + 撤销已注册命令（/clear 联动）
    def attach_registry(self, registry: CommandRegistry) -> None
    def set_pending(self, name: str, args: str) -> None    # use_skill 工具登记独立执行
    def take_pending(self) -> tuple[str, str] | None       # 主循环轮末取走
```

## 模块设计

### skills/model.py

职责：单文件解析与校验。frontmatter 用 `yaml.safe_load`；`name`/`description` 必填且类型正确；`tools` 须为字符串列表、`mode` 须为 `shared`/`independent`、`history` 须为非负整数、`model` 须为字符串——任一不合规 = 解析失败（返回 None + 原因）；未知字段忽略。正文原样保留（占位符激活时替换）。

### skills/discover.py

职责：三级扫描。每级目录扫 `*.md` 文件 + `*/SKILL.md`（目录型技能）；`skill.dir` = 目录路径。优先级：项目 > 用户 > 内置（静默覆盖）；同一级内按路径排序，重名后者跳过并警告；解析失败单个文件跳过并警告。`validate_whitelists(skills, registry_names) -> list[str]`：返回「技能 X 白名单引用未知工具 Y」问题列表（空 = 通过）。

### skills/manager.py

职责：激活与运行时行为。

- `activate`：查生效集 → **重读磁盘重新解析**（热更新；重读失败返回错误）→ 参数替换 → 存入激活表 → 命令注册（已 attach registry、未撞名、首次激活时）
- `active_snapshot`：拼【已激活技能】块（多个技能多个 `###` 小节，目录型附带「技能目录」行）
- `whitelist`：激活技能 `tools` 的并集；无任何白名单时 `whitelist_active()=False`
- `clear`：清激活表 + 撤销全部已注册技能命令
- `pending`：单槽位登记/取走（use_skill 工具登记 → 主循环轮末取走）

### skills/use_skill.py

职责：`UseSkillTool`。`execute`：`activate(name, args)` → 失败返回错误；shared → 返回激活确认 + **完整 SOP**（模型当轮即可见，不等下一轮系统提示）；independent → `set_pending` + 返回「技能 X 已激活（独立模式），本轮结束后自动执行，结果摘要回流本对话」。类属性：`read_only=True`（计划模式可见）、`timeout` 默认（不执行长任务）。

### agent/loop.py（改）

构造参数增 `skills_summary: str`、`skill_manager: SkillManager | None`（None = 行为与 ch12 一致，测试/嵌入兼容）。`_visible_tools()`：计划模式只读过滤 → 白名单过滤（白名单 ∪ {`use_skill`}）叠加。执行预检：`registry.get` 成功后、闸门裁决前，白名单外工具直接回灌失败结果「技能白名单未包含该工具: X」（AC9）。

### prompt/builder.py（改）

构造参数增 `skills_summary`、`skills_loader`（每轮现取，同 memory_loader 模式）。`stable = STABLE_TEXT + 【技能】块`（会话内不变，进缓存前缀，N5）；`build()` 的 dynamic 顺序变为：**补充块 → 【已激活技能】块 → 【记忆】 → 【自定义指令】**（钉在最显眼位置，F4）。

### commands/registry.py（改）

增 `unregister(name)`：移除命令及其别名索引；不存在时静默忽略。

### commands/builtins.py（改）

删 `/review`（`_cmd_review`、`REVIEW_PROMPT` 常量、注册条目），内置命令 12 → 11 条；review 技能承接原提示词。

### commands/ui.py（改）

UiController 增 `run_skill(self, name: str, args: str) -> None`：执行技能（按当前模式分流）。

### tui/uicontroller.py（改）

持有 `SkillManager` + 注入的 `run_independent` 回调；`run_skill`：`activate` 热更新 → shared：`display` 激活提示 + `send_user_message(SOP)`；independent：调 `run_independent(name, args)`。

### tui/app.py（改）

- `run()` 增 `skill_manager` 参数：agent 构造传入；`manager.attach_registry(command_registry)`；UiController 注入 `run_independent` 闭包
- 输入循环：每轮 `_run_agent_round` 返回后检查 `manager.take_pending()` → 有则 `_run_independent_skill`
- `_run_independent_skill(name, args)`：activate → 建 inner `ChatSession`（`restore(主对话最近 history 条)`）→ 建 inner `SkillManager`（共享技能字典、预激活该技能）→ `model` 派生 provider → inner `Agent`（同 gate/registry/context_window）→ `_agent_round(..., log=None, updater=None, prefix="技能 X 独立执行")`（流式渲染 + 权限弹窗走主循环，不落会话存档）→ 取最终助手回复为摘要 → 面板展示 → `_run_agent_round("【技能 X 独立执行摘要】\n...")` 自动续一轮
- `_agent_round` 返回 `RoundEnd`（调用方忽略即可，独立流程用其结果判断成败）

### cli.py（改）

装配顺序：`discover_skills(项目/用户/内置)` → 警告打 stderr → `SkillManager` → `registry.register(UseSkillTool(manager))` → `validate_whitelists`（有问题 stderr + exit 1）→ `build_registry()`（11 条）→ `run(..., skill_manager=manager, skills_summary=...)`。

## 模块交互

```
启动：cli → discover → SkillManager → UseSkillTool → registry
      → validate_whitelists（含 MCP 工具名集）→ Agent(manager) → TUI

shared（模型路径）：
      模型调 use_skill → manager.activate → 工具结果返回 SOP
      → 下一轮 build：active_snapshot 钉入补充块后；_visible_tools 收窄

independent（模型路径）：
      use_skill → activate + set_pending → 工具结果「本轮结束后执行」
      → RoundEnd → app 取 pending → _run_independent_skill：
        inner manager（预激活）→ inner Agent + inner session
        → _agent_round（流式/权限走主循环）→ 摘要面板
        → _run_agent_round(摘要) 自动续一轮

用户路径：/<name> → dispatch → handler → ui.run_skill → activate（热更新）
          → shared: send_user_message(SOP)；independent: _run_independent_skill

/clear：session.clear() + manager.clear()（撤销激活与命令）
```

## 文件组织

```
mewcode/
├── skills/
│   ├── __init__.py            — 包导出 + BUILTIN_SKILLS_DIR
│   ├── model.py               — Skill/SkillMode/解析/参数替换
│   ├── discover.py            — 三级扫描/覆盖/白名单校验
│   ├── manager.py             — SkillManager
│   ├── use_skill.py           — UseSkillTool
│   ├── commit.md              — 独立模式样板（git 流程）
│   ├── review.md              — 共享模式样板（承接 ch12 /review）
│   └── test/                  — 目录型样板
│       ├── SKILL.md
│       └── references/pytest-notes.md
├── agent/loop.py              — 修改：白名单收窄 + 执行拦截 + 构造参数
├── prompt/builder.py          — 修改：【技能】stable 块 +【已激活技能】dynamic 块
├── commands/registry.py       — 修改：unregister
├── commands/builtins.py       — 修改：删 /review
├── commands/ui.py             — 修改：run_skill
├── cli.py                     — 修改：技能发现/校验/装配
└── tui/
    ├── app.py                 — 修改：独立执行 runner + pending 触发
    └── uicontroller.py        — 修改：run_skill 实现

tests/
├── test_skills_model.py       — 解析/校验/占位符替换
├── test_skills_discover.py    — 三级覆盖/重名/坏文件/白名单校验
├── test_skills_manager.py     — 激活/热更新/白名单并集/pending/clear
├── test_use_skill_tool.py     — 工具行为（shared/independent/未知技能）
├── test_skills_agent.py       — _visible_tools 收窄/执行拦截/系统提示注入
└── test_skills_commands.py    — 命令注册/撞名跳过/撤销 + /review 迁移回归

pyproject.toml                 — 修改：package-data 打包 skills/**/*.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 独立执行时机 | use_skill 只登记 pending，本轮 RoundEnd 后由主循环执行 | 复用 `_agent_round` 事件渲染：独立执行流式可见、权限询问走主循环弹窗、不受工具 30 秒超时限制；若在工具调用内同步跑会长时间阻塞且无 UI |
| 摘要定义 | 独立对话的最终助手回复（失败时回流失败说明） | 零额外模型调用，行为确定；失败也回流并自动续一轮，让模型解释失败原因，流程不悬挂 |
| use_skill 返回内容 | 工具结果附带完整 SOP + 激活确认 | 模型当轮即可见完整指令，不必等下一轮系统提示重建；SOP 同时钉入环境上下文供后续轮次 |
| 技能列表位置 | stable 前缀追加【技能】块 | 会话内不变，进缓存前缀（N5）；已激活技能块在 dynamic、紧跟补充块之后（F4 最显眼位置） |
| 激活状态隔离 | 独立对话用独立 SkillManager（共享只读技能字典） | 独立对话内激活其他技能不污染主对话状态 |
| 热更新 | `activate` 每次从磁盘重读重解析 | 无文件监听开销；编辑文件后下次加载即生效（AC6） |
| model 字段 | `dataclasses.replace` 派生 ProviderConfig → `create_provider` 重建 | 复用同一配置的 base_url/api_key，仅换模型名；仅独立模式生效 |
| 命令撞名 | 注册前 `resolve` 检查，撞内置命令 → 跳过注册 + 警告 | 内置命令优先（AC13）；registry 增 `unregister` 支持 /clear 撤销技能命令 |
| 白名单校验时机 | MCP 注册 + use_skill 注册之后、启动 TUI 之前 | 白名单可引用 MCP 工具；引用不存在的工具启动即退出（N6） |
| 执行侧拦截 | 白名单激活期间非白名单工具回灌失败结果 | 可见性收窄之外的安全兜底（AC9，防模型幻觉调用） |
| use_skill 属性 | `read_only=True`、系统级 | 计划模式下可见；白名单过滤永远放行它 |
| 补全 | 不改 completion.py | `CommandCompleter` 每次实时读 `registry.visible()`，技能命令注册后自动出现（AC12） |
| 独立对话存档 | `log=None` 不落 JSONL | spec 明确：只有摘要进主历史（F6/F8） |
| 样板技能形态 | commit/review 单文件、test 目录型 | 同时覆盖两种文件形态，样板本身即文档 |
