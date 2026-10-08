# SubAgent 系统 Plan

## 架构概览

新包 `mewcode/agents/` 四模块 + 复用现有 Agent 类：

- **model.py**：AgentRole 数据类 + `parse_agent_file`（frontmatter 解析校验，参考 skills/model.py：类型非法返回 None+原因、未知字段忽略）
- **discover.py**：`discover_agents` 四级目录合并（插件→内置→用户→项目，从低到高同名覆盖；`*.md`/`*/AGENT.md` 双形态；失败跳过警告不阻断）
- **manager.py**：AgentTask/TaskManager——任务线程执行引擎（每任务专用线程+专属事件循环跑子 Agent）、同步等待（60s 超时自动转后台）、detach、完成通知队列、并发上限
- **内置角色**：`mewcode/agents/general.md`、`code-reviewer.md`（随包样板）

接入点：
- **tools/agent.py**：AgentTool（统一工具）——execute 里经 threading.local 取父上下文（loop.py 的 to_thread 包装注入），调 TaskManager.start；`timeout=70`（> 60 同步上限兜底）
- **agent/loop.py**：Agent 新增 `interactive` 参数（False 时 ASK 自动拒绝回灌）、`allowed_tools` 参数（工具名集合过滤）、`read_cache` 参数；to_thread 包装统一注入 threading.local（父上下文+读缓存）
- **tools/files.py**：read_file 读缓存（路径+mtime 键控，缓存实例经 threading.local 取用）
- **tui/app.py**：TaskManager 装配、轮末 drain 完成通知（注入主历史+自动续轮）、/tasks 命令
- **hooks/loader.py+actions.py**：subagent 动作扩展 role 字段、真实启动（模块级默认 TaskManager）
- **cli.py**：角色发现、TaskManager 装配、AgentTool 注册

## 核心数据结构

```python
# agents/model.py
@dataclass
class AgentRole:
    name: str                  # 唯一名（frontmatter 权威）
    description: str           # 一句话说明（工具错误提示展示）
    body: str                  # 正文 = 子 Agent 系统提示（自定义指令块）
    tools: tuple[str, ...] | None    # 白名单；None = 不收窄
    deny_tools: tuple[str, ...]      # 黑名单（再从结果集剔除）
    model: str | None                # None = 继承主对话 provider
    max_rounds: int                  # 最大轮次；缺省 30
    permission: str | None           # strict/default/accept；None = inherit
    source: Path                     # 文件路径（排错展示）
    level: str                       # project/user/builtin/plugin

# discover.py
@dataclass
class DiscoverResult:
    roles: dict[str, AgentRole]      # name → 生效角色（覆盖后）
    warnings: list[str]              # 解析失败/重名警告

# agents/manager.py
class TaskStatus(Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

@dataclass
class AgentTask:
    id: int                   # 任务序号（1 起，进程内唯一）
    kind: str                 # "defined" | "fork"
    role: str | None          # 角色名（fork 为 None）
    status: TaskStatus
    started_at: float         # time.monotonic
    finished_at: float | None
    usage_in: int             # token 用量（任务线程消费 UsageEvent 累计）
    usage_out: int
    result: str | None        # 最终回复 / 失败说明
    background: bool          # 是否已进入后台
    detached: bool            # 手动切后台标记
    done_event: threading.Event  # 完成信号（同步等待用）

@dataclass
class ParentContext:  # threading.local 载体（loop.py 注入，工具/hook 线程内读取）
    agent: "Agent"             # 父 Agent 引用（Fork 取消息/可见工具/档位）
    task_manager: "TaskManager"

class TaskManager:
    SYNC_TIMEOUT = 60.0        # 秒
    MAX_BACKGROUND = 5         # 同时后台任务上限
    def __init__(self, roles, provider, registry, hook_manager,
                 file_rules: list[Rule], project_root, user_dir, ...) -> None
    def start(self, kind, role, prompt, background, parent) -> ToolResult
        # 三条校验：type 合法（工具层做）、角色存在、后台数上限
    def detach(self, task_id: int) -> bool      # /tasks detach
    def snapshot(self) -> list[AgentTask]       # /tasks 展示
    def drain_completed(self) -> list[AgentTask]  # 轮末取走完成的后台任务
```

```python
# tools/agent.py
class AgentTool(Tool):
    name = "agent"
    read_only = False
    timeout = 70.0   # 略大于 60s 同步上限，兜底 run_tool 的线程 join
    def execute(self, args) -> ToolResult:
        # type/role/prompt/background 校验 → 取 ParentContext → TaskManager.start
        # 同步完成 → 返回子 Agent 最终回复（截断）
        # 转后台/detach → 返回「任务 #N 已转后台/已在后台」
```

Agent 新增构造参数：
```python
interactive: bool = True       # False：ASK 自动拒绝回灌、不发出权限询问事件
allowed_tools: set[str] | None = None  # 工具名集合过滤（子 Agent 工具集）
read_cache: FileReadCache | None = None  # read_file 缓存实例（隔离载体）
```

## 模块设计

### agents/model.py
**职责：** 角色 frontmatter 解析校验（F1）
**对外接口：** `parse_agent_file(path, level) -> tuple[AgentRole, None] | tuple[None, str]`（成功返回角色、失败返回原因）
**校验：** `name` 非空无空白；`description` 非空字符串；`tools`/`deny_tools` 字符串列表；`model` 字符串；`max_rounds` 正整数；`permission` 三值之一或 null；未知字段忽略
**依赖：** yaml（参考 skills/model.py 的 frontmatter 切分）

### agents/discover.py
**职责：** 四级目录扫描合并（F1）
**对外接口：** `discover_agents(project_root, user_dir, builtin_dir) -> DiscoverResult`
**合并：** 从低到高 `plugins/*/agents`（按插件名排序）→ builtin → user → project；同名静默覆盖；同层重名后者跳过并警告；解析失败警告不阻断
**依赖：** model.py

### agents/manager.py
**职责：** 任务生命周期与执行引擎（F3/F4/F6/F8/F9 的运行部分）
**任务线程：** `threading.Thread(daemon=True)` + `asyncio.new_event_loop`，线程内消费子 Agent 事件流（统计 UsageEvent 累计到 task、不渲染）；完成时：`status=DONE/FAILED`、`result=最终助手回复/失败说明`、`done_event.set()`；若已进后台 → 追加 `_completed` 队列
**同步等待**（AgentTool.execute 线程内调用 `wait_sync(task)`）：轮询 0.2s——完成→返回结果；detached→返回「已切后台」；超 60s→置 `background=True` 返回「已自动转后台」，任务线程继续跑
**start 校验**：角色不存在（defined）→失败结果含可用角色名；后台数超 5→失败结果
**子 Agent 构建**：defined=F3（空白会话+角色正文进 custom_instructions+allowed_tools=角色工具集）；fork=F4（`ChatSession().restore(父 messages)`+add_user(任务)、custom_instructions/skills_summary/memory_loader/env 与父一致保 stable 逐字节相同、allowed_tools=父可见工具名）；两者再应用三层防线过滤；gate=新 PermissionGate（新 RuleSet.add_all(文件规则)+角色档位或父档位）
**模型解析**：角色 model 为 None→父 provider；否则先按 config name 匹配 providers、再按模型 id 派生 ProviderConfig（ch13 replace 同款）
**对外接口：** `start(...)`、`wait_sync(task)`、`detach(id)`、`snapshot()`、`drain_completed()`
**依赖：** Agent、ChatSession、PermissionGate、hooks（子 Agent 共享 HookManager）

### tools/agent.py
**职责：** 统一 agent 工具（F2）
`execute`：解析校验 type/role/prompt（失败信息含合法取值/可用角色名）→ 从 threading.local 取 ParentContext（无上下文→失败「仅主对话可用」）→ `TaskManager.start`；同步任务 `wait_sync` 返回结果；后台任务立即返回「任务 #N 已在后台运行」
**依赖：** agents.manager、threading.local 父上下文

### tools/files.py 读缓存
- `FileReadCache`：`get(path, mtime) -> str | None`、`put(path, mtime, content)`，dict 键控，进程内有效
- `read_file.execute`：经 threading.local 取缓存实例（None 则直读磁盘——兼容存量测试）；命中（路径+mtime 一致）直接返回缓存内容；未命中读盘+写缓存
**依赖：** 无新增

### hooks/loader.py + actions.py 对接
- `HookAction` 增 `role: str = ""`；loader 校验：subagent 动作的 role 可选（空=缺省 general）
- `exec_subagent`：从 threading.local 取 ParentContext 的 task_manager（或模块级默认）→ 真实后台启动 defined 子 Agent（role 缺省 general、prompt 为任务）→ 返回 ok；两者都取不到 → 退回占位日志（兼容无装配场景）

### tui/app.py + commands/builtins.py
- `/tasks` 命令：`task_manager.snapshot()` 渲染状态表（编号/角色/状态/耗时/用量）；`/tasks detach <id>` 调 `detach`（ch14 /skill 同款闭包注册模式）
- 轮末 drain：`drain_completed()` 每条结果构造通知文本（`【子 Agent 结果：{角色名}（任务 #{id}）】\n{结果}`）注入 `session.add_user`，全部注入后自动续一轮（`_run_agent_round`），与 ch13 `_drain_pending_independent` 同位置同模式
- TaskManager 装配在 cli：角色发现→文件规则副本→构造→AgentTool 注册→tui.run 传入

## 模块交互

```
主 Agent 调用 agent 工具:
  loop.py to_thread 包装 ──设 threading.local──▶ ParentContext(agent, task_manager)
  AgentTool.execute ──▶ TaskManager.start(kind, role, prompt, background, parent)
     ├─ defined: 空白会话 + 角色正文 + 角色工具集（三层防线）
     ├─ fork:    父消息全量 restore + 父 stable 参数 + 父可见工具
     └─ 任务线程: asyncio 循环跑子 Agent（事件流消费、用量累计、Hook 触发）

  同步等待（工具线程内 wait_sync）:
     ┌─ 完成（≤60s）→ 工具结果 = 子 Agent 最终回复
     ├─ 60s 超时    → background=True，工具结果 =「已转后台，任务 #N」
     └─ /tasks detach → detached=True，工具结果 =「已切后台，任务 #N」

  后台任务完成:
     task 线程 → status=DONE/FAILED → _completed 队列
     主对话轮末（tui drain）→ 结果注入 session.add_user
     → 自动续一轮 → 主 Agent 汇报/继续
```

## 文件组织

```
mewcode/agents/                       （新包）
├── __init__.py       — 导出 AgentRole/DiscoverResult/discover_agents/TaskManager/TaskStatus
├── model.py          — AgentRole + parse_agent_file（F1）
├── discover.py       — 四级目录扫描合并（F1）
├── manager.py        — AgentTask/TaskManager/任务线程引擎（F3/F4/F6/F8）
├── general.md        — 内置角色：通用助手（hook subagent 缺省）
└── code-reviewer.md  — 内置角色：代码审查样板

修改：
├── mewcode/tools/agent.py      — AgentTool（统一工具，F2）
├── mewcode/tools/__init__.py   — create_registry 注册 AgentTool（可选参数）
├── mewcode/tools/files.py      — FileReadCache + read_file 缓存接入（F5）
├── mewcode/agent/loop.py       — Agent 增 interactive/allowed_tools/read_cache；
│                                  ASK 自动拒绝分支；to_thread 包装注入 threading.local
├── mewcode/permissions/rules.py — RuleSet 增 add_all（子 Agent 文件规则复制用）
├── mewcode/hooks/loader.py     — HookAction 增 role；subagent 校验扩展（F10）
├── mewcode/hooks/actions.py    — exec_subagent 真实启动（F10）
├── mewcode/commands/builtins.py — /tasks 与 /tasks detach 命令（F8）
├── mewcode/tui/app.py          — TaskManager 传入、轮末 drain 注入+续轮、ParentContext 注入点
└── mewcode/cli.py              — 角色发现、TaskManager 装配、AgentTool 注册、文件规则副本

tests/                              （新）
├── test_agents_model.py       — 解析校验各字段与非法样例
├── test_agents_discover.py    — 四级覆盖、重名、坏文件跳过
├── test_agents_manager.py     — 定义式/Fork 构建、三层防线、60s 转后台、上限、detach、
│                                 token 回报、完成队列
├── test_agent_tool.py         — 参数校验、同步返回结果、后台返回任务号、无父上下文失败
├── test_agents_loop.py        — interactive ASK 自动拒绝、allowed_tools 过滤、读缓存实例隔离
├── test_files_cache.py        — mtime 键控命中/失效
├── test_tasks_command.py      — /tasks 渲染与 detach
└── test_hooks_subagent.py     — subagent 动作真实启动与 role 缺省
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 子 Agent 执行引擎 | 每任务专用线程 + 专属事件循环；同步/后台只差等待方式 | 统一执行路径；60s 超时=轮询截止、detach=解除等待；provider 泵线程机制现成支持 |
| 60s 转后台实现 | AgentTool.execute 内 `wait_sync` 轮询 0.2s 截止 60s；`tool.timeout=70` 兜底 run_tool 的 join | 超时转后台无需中断线程——任务线程继续跑，工具返回「已转后台」 |
| 父上下文传递 | threading.local（loop.py 的 to_thread 包装注入 ParentContext） | 工具 execute 与 hook 动作在线程内天然拿到父 Agent 与 TaskManager，不改工具接口签名 |
| 读缓存注入 | threading.local 同款（每 to_thread 入口设置 Agent 实例缓存） | 并发 gather 多线程各自设置；registry 单例不改 |
| 文件规则共享 | RuleSet 增 add_all；cli 保留文件规则副本，子 Agent 新建 RuleSet 只装文件规则 | 会话规则不传入（独立追踪）、文件规则共享（静态） |
| Fork 会话构造 | `ChatSession().restore(父 messages)` + add_user(任务) | ch13 先例；消息逐字节一致 → 前缀缓存命中 |
| Fork stable 一致 | 子 Agent 复用父的 custom_instructions/skills_summary/memory_loader/env 构造 builder | stable 逐字节相同是缓存命中的前提 |
| 工具集限制 | Agent 增 `allowed_tools: set[str]` 过滤 _visible_tools | 比复用 skill_manager 白名单机制更直接，语义清晰 |
| ASK 自动拒绝 | Agent 增 `interactive=False`：ASK 分支不发出询问事件、直接 failure 回灌 | 主循环零挂起风险；回灌原因让模型调整 |
| 完成通知注入 | 轮末 drain 合并注入 + 自动续一轮（ch13 同款位置） | 主对话运行中不打断；空闲时自然处理 |
| hook subagent 对接 | threading.local 优先 + 模块级默认 TaskManager；取不到退占位日志 | 装配层注册默认；无装配场景（测试/嵌入）兼容 ch15 行为 |
| 内置角色 | general + code-reviewer 两个样板 | 覆盖 hook 缺省角色与最常见审查场景，随包分发 |
| 后台上限 | 5 个，start 时检查拒绝 | 防后台任务爆炸（N5）；常量可调 |
