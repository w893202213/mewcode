# SubAgent 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/agents/__init__.py` | 包导出 |
| 新建 | `mewcode/agents/context.py` | ParentContext + threading.local 载体 |
| 新建 | `mewcode/agents/model.py` | AgentRole + parse_agent_file |
| 新建 | `mewcode/agents/discover.py` | 四级目录扫描合并 |
| 新建 | `mewcode/agents/manager.py` | AgentTask/TaskManager/任务线程引擎 |
| 新建 | `mewcode/agents/general.md` | 内置角色：通用助手 |
| 新建 | `mewcode/agents/code-reviewer.md` | 内置角色：代码审查样板 |
| 新建 | `mewcode/tools/agent.py` | AgentTool 统一工具 |
| 新建 | `tests/test_agents_model.py` | 角色解析测试 |
| 新建 | `tests/test_agents_discover.py` | 四级加载测试 |
| 新建 | `tests/test_files_cache.py` | 读缓存测试 |
| 新建 | `tests/test_agents_loop.py` | interactive/工具过滤/缓存隔离测试 |
| 新建 | `tests/test_agents_manager.py` | 任务管理器测试 |
| 新建 | `tests/test_agent_tool.py` | agent 工具测试 |
| 新建 | `tests/test_hooks_subagent.py` | subagent 动作对接测试 |
| 新建 | `tests/test_tasks_command.py` | /tasks 命令测试 |
| 修改 | `mewcode/tools/files.py` | FileReadCache + read_file 缓存接入 |
| 修改 | `mewcode/tools/__init__.py` | create_registry 注册 AgentTool |
| 修改 | `mewcode/agent/loop.py` | interactive/allowed_tools/read_cache + ASK 拒绝 + 线程注入 |
| 修改 | `mewcode/permissions/rules.py` | RuleSet 增 add_all |
| 修改 | `mewcode/hooks/loader.py`、`mewcode/hooks/actions.py` | subagent 动作 role 字段 + 真实启动 |
| 修改 | `mewcode/commands/builtins.py` | /tasks 与 /tasks detach |
| 修改 | `mewcode/tui/app.py` | TaskManager 传入、轮末 drain 注入+续轮 |
| 修改 | `mewcode/cli.py` | 角色发现、TaskManager 装配、AgentTool 注册、文件规则副本 |

## T1: agents 包骨架 + context.py

**文件：** `mewcode/agents/__init__.py`、`mewcode/agents/context.py`
**依赖：** 无
**步骤：**
1. 建 `mewcode/agents/__init__.py`：模块 docstring（「SubAgent 系统：主 Agent 委派子任务的独立执行单元」），导出后续补
2. `context.py`：`ParentContext` dataclass（字段 `agent`、`task_manager`、`read_cache`，均缺省 None，类型用 Any 避免与 loop.py/manager.py 循环依赖）；模块级 `_local = threading.local()`；`push(ctx)` / `current() -> ParentContext | None` / `clear()` 三个函数
**验证：** `python3 -m compileall mewcode/agents/` 无报错

## T2: model.py 角色解析

**文件：** `mewcode/agents/model.py`
**依赖：** T1
**步骤：**
1. `AgentRole` dataclass：name/description/body/tools(tuple|None)/deny_tools(tuple)/model(str|None)/max_rounds(int=30)/permission(str|None)/source(Path)/level(str)
2. `parse_agent_file(path, level) -> tuple[AgentRole, None] | tuple[None, str]`：frontmatter 切分（参考 skills/model.py 的 `---` 分割）；校验——name 非空不含空白、description 非空字符串、tools/deny_tools 为字符串列表（None 允许）、model 字符串、max_rounds 正整数（缺省 30）、permission 在 {strict, default, accept} 或 None、未知字段忽略；失败返回 (None, 中文原因)
**验证：** `python3 -m compileall mewcode/agents/` 无报错

## T3: test_agents_model.py

**文件：** `tests/test_agents_model.py`
**依赖：** T2
**步骤：**
1. 合法样例：全字段角色解析成功，各字段值正确；缺省值（max_rounds=30、permission=None、tools=None、deny_tools 空）
2. 只含必填字段的最小角色解析成功
3. 非法样例各返回 (None, 原因)：缺 name、name 含空白、缺 description、tools 非列表、permission 非法值、max_rounds 为 0/字符串
4. 未知字段忽略；正文（含空行）完整保留
**验证：** `.venv/bin/python -m pytest tests/test_agents_model.py -q` 全过

## T4: discover.py 四级加载 + 内置角色

**文件：** `mewcode/agents/discover.py`、`mewcode/agents/general.md`、`mewcode/agents/code-reviewer.md`
**依赖：** T2
**步骤：**
1. `DiscoverResult(roles: dict[str, AgentRole], warnings: list[str])`；`discover_agents(project_root, user_dir, builtin_dir) -> DiscoverResult`
2. 扫描层级从低到高：`user_dir/plugins/*/agents/`（glob，按插件目录名排序）→ builtin → `user_dir/agents/` → `project_root/.mewcode/agents/`；每层支持 `*.md` 与 `*/AGENT.md` 双形态；同名静默覆盖（高优先级胜）；同层重名后者跳过并警告；解析失败警告不阻断
3. 内置角色文件：`general.md`（通用助手：可用全部工具、以简洁中文回复、完成即停）；`code-reviewer.md`（代码审查：只读工具白名单 read_file/grep_code/find_files/run_command、审查立场：找 bug 与简化机会）
**验证：** `python3 -m compileall mewcode/agents/ -q` 无报错

## T5: test_agents_discover.py

**文件：** `tests/test_agents_discover.py`
**依赖：** T4
**步骤：**
1. tmp_path 构造项目/用户/插件三层目录各放同名角色（不同正文）→ 断言生效角色取项目级版本
2. 内置层（真实 `mewcode/agents/`）在无用户/项目覆盖时生效（general/code-reviewer 存在）
3. 同层两个同名文件 → 后者跳过且 warnings 含提示；坏文件（缺 name）跳过且 warnings 含原因、其余照常加载
4. 目录型 `*/AGENT.md` 被识别；无 agents 目录时结果为空 roles
**验证：** `.venv/bin/python -m pytest tests/test_agents_discover.py -q` 全过

## T6: FileReadCache + read_file 接入

**文件：** `mewcode/tools/files.py`
**依赖：** T1（context）
**步骤：**
1. `FileReadCache` 类：`get(path: str, mtime: float) -> str | None`、`put(path, mtime, content)`；内部 dict 以 path 为键存 (mtime, content)；进程内有效
2. `read_file.execute`：经 `agents.context.current()` 取 read_cache（None 则直读磁盘——兼容存量行为）；命中（键存在且 mtime 一致）直接返回缓存内容；未命中读盘后 put；路径读取失败不写缓存
3. 注意：mtime 在读取时现取（`Path.stat()`），写缓存时用同一 mtime
**验证：** `python3 -m compileall mewcode/tools/` 无报错 + 存量 test_tools 相关用例不破（`.venv/bin/python -m pytest tests/test_tools.py -q`）

## T7: test_files_cache.py

**文件：** `tests/test_files_cache.py`
**依赖：** T6
**步骤：**
1. push 带缓存的 ParentContext → 两次 read_file 同文件（内容不变）断言第二次命中（改磁盘内容但 mtime 伪造不变→仍返回旧内容）
2. 文件真实修改（内容变化+mtime 变化）→ 返回新内容
3. 无上下文（缓存 None）→ 直读磁盘行为不变
4. 两个不同缓存实例互不可见（A 缓存有记录、B 无）
**验证：** `.venv/bin/python -m pytest tests/test_files_cache.py -q` 全过

## T8: RuleSet.add_all

**文件：** `mewcode/permissions/rules.py`
**依赖：** 无
**步骤：**
1. `add_all(rules: list[Rule]) -> None`：批量追加（不进 `_file_rules`——子 Agent 复制文件规则用，validate_tools 不重复校验）
**验证：** `.venv/bin/python -m pytest tests/test_registry.py tests/test_permission_loop.py -q` 存量不破

## T9: loop.py 改造

**文件：** `mewcode/agent/loop.py`
**依赖：** T1、T6
**步骤：**
1. `__init__` 增三个参数：`interactive: bool = True`、`allowed_tools: set[str] | None = None`、`read_cache: FileReadCache | None = None`；存成员 `_interactive`/`_allowed_tools`/`_read_cache`
2. `_visible_tools()`：`allowed_tools` 非 None 时过滤 `tools = [t for t in tools if t.name in self._allowed_tools]`
3. ASK 分支：`not self._interactive` 时——不发 PermissionAskEvent、不挂起，直接 `results[id(call)] = ToolResult.failure("非交互模式无法人工确认，已拒绝")` 并 continue（放在闸门 ASK 判定处）
4. to_thread 包装统一注入：只读并发 gather 与副作用循环的 `execute_call(self._registry, c)` 换成私有方法 `_run_call(c)`：`push(ParentContext(agent=self, read_cache=self._read_cache))` → `execute_call` → finally `clear()`；`_run_call` 为普通同步函数（to_thread 参数）
5. Agent 增 `subagent_context()` 只读方法：返回（messages 副本、`_visible_tools()` 结果、当前 `gate.mode`、`custom_instructions`、`skills_summary`、`memory_loader`、builder 的 env 参数）——为 T11 Fork 构建供数据；构造函数里把 custom_instructions/skills_summary/memory_loader 同时存成员
**验证：** `.venv/bin/python -m pytest -q` 全量存量不破

## T10: test_agents_loop.py

**文件：** `tests/test_agents_loop.py`
**依赖：** T9
**步骤：**
1. interactive=False + default 档：副作用工具调用不产生 PermissionAskEvent、结果失败含「非交互模式无法人工确认」；模型第二轮改只读工具完成
2. interactive=True 存量行为不变（有 PermissionAskEvent、可应答放行）
3. allowed_tools={"read_file"}：模型请求 run_command 时可见工具列表只含 read_file（FakeProvider handler 里断言 tools 名字集合）
4. 读缓存隔离：父 Agent 缓存读过文件后，子 Agent（新 Agent 实例新缓存）读同文件不命中父缓存（两缓存对象断言互不可见）
**验证：** `.venv/bin/python -m pytest tests/test_agents_loop.py -q` 全过

## T11: manager.py 构建逻辑

**文件：** `mewcode/agents/manager.py`（第一部分）
**依赖：** T9、T8
**步骤：**
1. `TaskStatus(Enum)`：RUNNING/DONE/FAILED；`AgentTask` dataclass（字段见 plan：id/kind/role/status/started_at/finished_at/usage_in/usage_out/result/background/detached/done_event）
2. `TaskManager.__init__(roles: dict, providers: list[ProviderConfig], registry, hook_manager, file_rules: list[Rule], project_root: Path, user_dir: Path)`：存成员、`_tasks: list`、`_completed: queue.Queue`、`_next_id = 1`
3. `_build_role_agent(role, prompt)`（defined）：`ChatSession()` 空白 + add_user(prompt)；custom_instructions=role.body；gate=新 PermissionGate(root, RuleSet()+add_all(file_rules), mode=permission 解析（None→父档位）)；provider=模型解析（None→父 provider；否则按 providers 名字匹配、失败按模型 id 派生）；max_iterations=role.max_rounds；allowed_tools=角色工具集（tools 非空→其与 registry 名字交集；否则全部）再减 deny_tools 再减 {"agent"}；interactive=False；read_cache=新实例；hook_manager 共享
4. `_build_fork_agent(prompt, parent_ctx)`（fork）：`ChatSession().restore(父 messages)` + add_user(prompt)；custom_instructions/skills_summary/memory_loader/env 取父的 subagent_context；provider=父 provider；allowed_tools=父可见工具名减 {"agent"}；gate 独立（父当前档位 + 文件规则）
5. `start(kind, role, prompt, background, parent_agent) -> tuple[AgentTask | None, str]`：校验（defined 角色不存在→(None, 可用角色名列表)；后台数超 MAX_BACKGROUND→(None, 原因)）→ 构造 task + 构建子 Agent（构建失败→(None, 原因)）→ 建任务线程跑（T12 的 `_task_main`）→ 返回 (task, "")
**验证：** `python3 -m compileall mewcode/agents/ -q` 无报错

## T12: manager.py 执行引擎

**文件：** `mewcode/agents/manager.py`（第二部分）
**依赖：** T11
**步骤：**
1. `_task_main(task, agent)`：线程体——`asyncio.run(_consume(agent, task))`；`_consume` 消费事件流：UsageEvent 累计 usage_in/out（取最后值）、收集最终助手文本（collector 或事件累积）；结束后 status=DONE、result=最终回复；异常→status=FAILED、result=说明；finally：finished_at、done_event.set()、若 background→`_completed.put(task)`
2. `wait_sync(task) -> str`（工具线程调用）：轮询 0.2s 至 60s——status 非 RUNNING→返回结果文本；detached→返回「任务 #N 已切后台」；超时→置 background=True 返回「任务 #N 同步超 60 秒已自动转后台」
3. `detach(task_id) -> bool`：找 RUNNING 且未 background 任务→置 detached=True、background=True
4. `snapshot() -> list[AgentTask]`：副本列表（按 id 排序）；`drain_completed() -> list[AgentTask]`：清空 `_completed`
5. 后台计数 = status==RUNNING 且 background 的任务数
**验证：** `python3 -m compileall mewcode/agents/ -q` 无报错

## T13: test_agents_manager.py

**文件：** `tests/test_agents_manager.py`
**依赖：** T12
**步骤：**
1. 构造 FakeProvider（同 test_agent_loop 基建）注入 TaskManager（provider 为 FakeProvider 直接传入）
2. defined 启动：空白会话（子 Agent 系统提示含角色正文——FakeProvider.systems 断言 custom 块）、工具集=白名单且无 agent 工具（handler 里断言 tools 名字）
3. fork 启动：子会话消息=父全部+任务消息（task.done 后断言）；首请求 messages 序列与父一致；allowed_tools=父可见工具
4. 三层防线：角色 tools 含 agent → 结果集无 agent；deny_tools 剔除生效；background 任务工具集无 agent/use_skill
5. wait_sync：快速完成任务返回结果；60s 超时用慢任务（FakeProvider 首轮 sleep 模拟？——用假 provider 阻塞：handler 里 time.sleep 或 provider.stream 阻塞）→ 置 background、返回「已自动转后台」、任务最终完成进 _completed
6. detach：detach 后 wait_sync 立即返回「已切后台」
7. 后台上限：直接 start 5 个 background 慢任务后第 6 个返回失败原因
8. token 回报：UsageEvent 值写入 task.usage_in/out
**验证：** `.venv/bin/python -m pytest tests/test_agents_manager.py -q` 全过

## T14: tools/agent.py + 注册

**文件：** `mewcode/tools/agent.py`、`mewcode/tools/__init__.py`
**依赖：** T12
**步骤：**
1. `AgentTool(Tool)`：name="agent"、read_only=False、timeout=70.0；input_schema：type(enum defined/fork)/role/prompt/background(boolean)，required=[type, prompt]
2. `execute(args)`：type 不在枚举→failure（含合法取值）；prompt 缺失/空→failure；defined 且 role 缺失→failure；`ParentContext = context.current()` 为 None→failure「仅主对话可用」；`task_manager.start(kind, role, prompt, background, parent)` → 失败→failure(原因)；background（或 fork 强制）→success(f"任务 #{id} 已在后台运行")；否则 `wait_sync(task)` → 结果文本→success（截断）
3. fork：background 参数强制 true（忽略 false）
4. `create_registry` 增可选参数注册 AgentTool（缺省不注册——存量测试兼容；装配层显式传入）
**验证：** `python3 -m compileall mewcode/tools/ -q` 无报错 + 全量存量不破

## T15: test_agent_tool.py

**文件：** `tests/test_agent_tool.py`
**依赖：** T14
**步骤：**
1. 非法 type→failure 含「defined/fork」；prompt 缺失→failure；defined 无 role→failure；角色不存在→failure 含可用角色名
2. 无 ParentContext→failure「仅主对话可用」
3. 有上下文 + 快速 FakeProvider：同步执行返回子 Agent 最终回复
4. background=true 立即返回任务编号（不等待完成）；fork + background=false 仍进后台（返回任务编号）
5. 工具列表断言：schema 的 required 与 enum 正确
**验证：** `.venv/bin/python -m pytest tests/test_agent_tool.py -q` 全过

## T16: hooks subagent 动作对接

**文件：** `mewcode/hooks/loader.py`、`mewcode/hooks/actions.py`
**依赖：** T12
**步骤：**
1. `HookAction` 增 `role: str = ""` 字段；loader 的 subagent 分支：role 可选（空=缺省 general），非字符串报错
2. `exec_subagent(action)` 改真实启动：取 `context.current()` 的 task_manager——有且角色存在→`start("defined", role or "general", action.prompt, background=True, parent)`→ok（message=「已启动子 Agent（任务 #N）」或失败原因）；无 task_manager 或角色不存在→占位日志 + 返回 ok（保持 ch15「Hook 失败不中断」语义）
3. 注意：子 Agent 线程内 task_manager 为 None→自然退回占位，防嵌套（实现细化：不用模块级默认，避免子 Agent 内再启动）
**验证：** `python3 -m compileall mewcode/hooks/ -q` + 存量 test_hooks_* 不破（`.venv/bin/python -m pytest tests/test_hooks_loader.py tests/test_hooks_actions.py -q`）

## T17: test_hooks_subagent.py

**文件：** `tests/test_hooks_subagent.py`
**依赖：** T16
**步骤：**
1. push 带 task_manager 的上下文（FakeProvider 注入的 TaskManager）→ exec_subagent 返回 ok 且 message 含「任务 #」；任务完成进 _completed
2. role 缺省→用 general（任务 role 字段断言）；role 指定→用指定角色
3. 无上下文→占位日志（caplog 断言「占位」）且返回 ok
4. loader：subagent 动作带 role 字段解析成功；role 非字符串报错
**验证：** `.venv/bin/python -m pytest tests/test_hooks_subagent.py -q` 全过

## T18: /tasks 命令

**文件：** `mewcode/commands/builtins.py`
**依赖：** T12
**步骤：**
1. `render_task_list(snapshot) -> str` 纯函数：空表「没有进行中的子 Agent 任务」；否则逐行「#id 角色/状态/耗时/输入token/输出token」+ 完成任务的 result 摘要一行
2. `make_tasks_handler(task_manager)` 闭包（ch14 /skill 同款）：解析 `/tasks detach <id>`（id 非法/不存在→提示）；纯 `/tasks`→渲染列表；register 到 builtins
**验证：** `python3 -m compileall mewcode/commands/ -q` 无报错

## T19: test_tasks_command.py

**文件：** `tests/test_tasks_command.py`
**依赖：** T18
**步骤：**
1. render_task_list：空表提示；含 running/done 任务的渲染含状态与用量
2. handler：/tasks detach 合法 id→detach 调用生效（任务 detached）；非法 id→错误提示
3. 命令注册：/tasks 在命令列表（/help 输出含 tasks）
**验证：** `.venv/bin/python -m pytest tests/test_tasks_command.py -q` 全过

## T20: tui + cli 装配

**文件：** `mewcode/tui/app.py`、`mewcode/cli.py`
**依赖：** T14、T16、T18
**步骤：**
1. cli：`discover_agents(project_root, USER_DIR, BUILTIN_AGENTS_DIR)`（警告打印）→ `TaskManager(roles, configs, registry, hook_manager, 文件规则副本, root, USER_DIR)`；文件规则副本从 `_build_gate` 的 RuleSet 取（`_build_gate` 返回 (gate, file_rules)）；`registry.register(AgentTool(task_manager))`（use_skill 注册后）；`run(...)` 传 task_manager
2. tui.run 增 `task_manager: TaskManager | None = None` 参数；`Agent(...)` 主 Agent 构造传 task_manager（Agent 增同名参数存成员——注意与 T9 的 ParentContext 注入联动：主 Agent 的 `_run_call` 里 `ParentContext(agent=self, task_manager=self._task_manager, read_cache=...)`）；注册 /tasks 命令（command_registry 动态注册，ch14 模式）
3. 轮末 drain：`_drain_pending_independent()` 旁新增 `_drain_completed_tasks()`——`task_manager.drain_completed()` 每条构造通知文本 `【子 Agent 结果：{角色}（任务 #{id}）】\n{result}` 注入 `session.add_user` + 打印提示；有注入则自动 `_run_agent_round` 一轮
4. Agent 构造签名：`task_manager: TaskManager | None = None`（缺省 None 兼容）
**验证：** `.venv/bin/python -m pytest -q` 全量通过 + `python3 -m compileall mewcode/` + `.venv/bin/python -m mewcode --help` 正常

## T21: 全量回归

**依赖：** T20
**步骤：**
1. `.venv/bin/python -m pytest -q` 全量通过（ch15 存量 664 + 新增 ch16 用例）
2. `.venv/bin/python -m mewcode --help` 正常
3. 提交代码（与 T20 合并提交）

**验证：** 全量测试通过、--help 正常

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13
→ T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21
```
（线性执行；T4 与 T6-T8 无相互依赖但保持线性便于提交追踪）

**提交点：** T3 后（model）、T5 后（discover+内置）、T7 后（读缓存）、T10 后（loop 改造）、T13 后（manager）、T15 后（agent 工具）、T17 后（hooks 对接）、T19 后（/tasks）、T21 后（装配+回归）

**实现细化说明：** T16 取消 plan 中「模块级默认 TaskManager」——exec_subagent 只认 threading.local 上下文（主 Agent 工具执行线程内有效），取不到退占位日志；这同时天然防住子 Agent 线程内嵌套启动（子 Agent 的 task_manager 为 None）。
