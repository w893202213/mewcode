# ch13 技能系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/skills/__init__.py` | 包导出 + `BUILTIN_SKILLS_DIR` |
| 新建 | `mewcode/skills/model.py` | Skill/SkillMode、frontmatter 解析校验、参数替换 |
| 新建 | `mewcode/skills/discover.py` | 三级扫描、优先级覆盖、白名单启动校验、技能摘要格式化 |
| 新建 | `mewcode/skills/manager.py` | SkillManager：激活/快照/白名单/pending/命令注册撤销 |
| 新建 | `mewcode/skills/use_skill.py` | UseSkillTool 系统级工具 |
| 新建 | `mewcode/skills/commit.md` | 独立模式样板（git 提交流程） |
| 新建 | `mewcode/skills/review.md` | 共享模式样板（承接 ch12 /review 提示词） |
| 新建 | `mewcode/skills/test/SKILL.md` | 目录型样板入口 |
| 新建 | `mewcode/skills/test/references/pytest-notes.md` | 目录型样板的参考文档 |
| 修改 | `mewcode/prompt/builder.py` | 【技能】stable 块 +【已激活技能】dynamic 块 |
| 修改 | `mewcode/agent/loop.py` | 构造参数、_visible_tools 白名单、执行侧拦截 |
| 修改 | `mewcode/commands/registry.py` | unregister + 注册/索引线程安全 |
| 修改 | `mewcode/commands/builtins.py` | 删 /review（12→11 条） |
| 修改 | `mewcode/commands/ui.py` | UiController 增 run_skill |
| 修改 | `mewcode/cli.py` | 技能发现/校验/装配 |
| 修改 | `mewcode/tui/app.py` | 独立执行 runner、pending 触发、_agent_round 返回 end+prefix |
| 修改 | `mewcode/tui/uicontroller.py` | run_skill 实现 |
| 修改 | `pyproject.toml` | package-data 打包 skills/**/*.md |
| 修改 | `scripts/mock_llm.py` | 增 skill-commit-model（e2e 用） |
| 新建 | `tests/test_skills_model.py` | 解析/校验/占位符 |
| 新建 | `tests/test_skills_discover.py` | 三级覆盖/重名/坏文件/白名单校验/内置样板 |
| 新建 | `tests/test_skills_manager.py` | 激活/热更新/白名单并集/pending/clear |
| 新建 | `tests/test_use_skill_tool.py` | 工具行为 |
| 新建 | `tests/test_skills_builder.py` | 系统提示注入位置与顺序 |
| 新建 | `tests/test_skills_agent.py` | _visible_tools 收窄/执行拦截 |
| 新建 | `tests/test_skills_commands.py` | 命令注册/撞名/撤销 |
| 修改 | `tests/test_command_*.py` 等 | /review 相关断言更新（12→11 条） |

## T1: 技能数据模型与解析

**文件：** `mewcode/skills/__init__.py`、`mewcode/skills/model.py`；新建 `tests/test_skills_model.py`
**依赖：** 无
**步骤：**
1. `__init__.py`：`BUILTIN_SKILLS_DIR = Path(__file__).parent`；导出 Skill、SkillMode、parse_skill_file、substitute_args
2. `model.py`：`SkillMode` 枚举（shared/independent）；`Skill` 冻结数据类（字段见 plan：name/description/body/tools/mode/history/model/dir/source/level）
3. `parse_skill_file(path, level, dir=None) -> (Skill | None, str | None)`：读文件（失败返回原因）；提取 frontmatter（首行 `---` 起、下一个 `---` 止；缺失或 YAML 解析失败返回原因）；字段校验——name/description 必填非空字符串、name 不含空白；tools 为 None 或字符串列表；mode 缺省 shared、非法值报错；history 缺省 0、须非负整数且非 bool；model 为 None 或非空字符串；未知字段忽略；正文原样保留
4. `substitute_args(body, args)`：先替换 `$ARGUMENTS` 再替换 `$ARGS`（避免前缀误伤），全部替换为 args
**验证：** `python -m pytest tests/test_skills_model.py -q` 全过；`python -m compileall mewcode/skills` 无错误

## T2: 三级扫描与优先级覆盖

**文件：** `mewcode/skills/discover.py`；新建 `tests/test_skills_discover.py`
**依赖：** T1
**步骤：**
1. `_scan_dir(dir, level)`：`*.md` 文件 + `*/SKILL.md` 子目录（目录型，`dir=` 目录路径）逐个解析；解析失败 → 警告「技能文件 X 解析失败: 原因，已跳过」；同层重名 → 按路径排序，后者警告「同层重名，已跳过」
2. `discover_skills(project_root, user_dir, builtin_dir) -> DiscoverResult`：按内置 → 用户 → 项目顺序合并（高级别覆盖低级别，静默）；每层目录不存在时跳过；结果按名字排序
3. `validate_whitelists(skills, registry_names) -> list[str]`：生效集中 tools 引用不存在工具名的 → 「技能 X 白名单引用未知工具: Y」
4. `skills_summary(skills) -> str`：`- name: description` 行（按名排序），空集返回空串
**验证：** `python -m pytest tests/test_skills_discover.py -q` 全过

## T3: SkillManager

**文件：** `mewcode/skills/manager.py`；新建 `tests/test_skills_manager.py`
**依赖：** T1
**步骤：**
1. `__init__(skills: dict[str, Skill])`；`skills` 属性公开（独立执行共享字典用）
2. `activate(name, args="") -> ActivationResult`：未知技能 → error；**重读磁盘** `parse_skill_file(skill.source, skill.level, skill.dir)`（热更新；重读失败 → error「技能 X 重读失败: 原因」）；`substitute_args` 得 body；存入激活表；首次激活且已 attach registry 且未撞名（`registry.resolve(name) is None`）→ 注册 `CommandSpec(name, description, usage=f"/{name} [参数]", type=PROMPT, handler=lambda parsed, ui: ui.run_skill(name, parsed.args))`；撞名 → 警告「技能命令 /X 与内置命令撞名，跳过注册」；返回 first_time
3. `active_snapshot()`：空 → ""；否则 `【已激活技能】` 标题 + 每技能 `### {name}` +（目录型）`技能目录: {dir}` + body；按激活顺序输出
4. `whitelist()`：激活技能 tools 的并集（忽略 tools 为 None 的）；`whitelist_active()`：存在激活技能且 tools 非 None；`is_allowed(name)`：无收窄时恒 True
5. `set_pending(name, args)` / `take_pending()`：单槽位登记/取走
6. `clear()`：清激活表 + 撤销全部已注册命令（记录注册过的名字集合）+ 清 pending
7. `attach_registry(registry)`：绑定命令注册中心
**验证：** `python -m pytest tests/test_skills_manager.py -q` 全过

## T4: use_skill 系统级工具

**文件：** `mewcode/skills/use_skill.py`；新建 `tests/test_use_skill_tool.py`
**依赖：** T3
**步骤：**
1. `UseSkillTool(Tool)`：`name = "use_skill"`、`read_only = True`（计划模式可见）；`description` 说明用途与参数；`input_schema`：name 必填字符串、args 可选字符串缺省 ""
2. `execute(args)`：`manager.activate(name, args)` → 失败 `ToolResult.failure(error)`；shared → `success("技能 X 已激活，以下为完整指令（已注入环境上下文，参数已替换）：\n\n" + body)`；independent → `set_pending(name, args)` + `success("技能 X 已激活（独立模式），本轮结束后自动执行，结果摘要回流本对话。")`
**验证：** `python -m pytest tests/test_use_skill_tool.py -q` 全过

## T5: 系统提示拼装改造

**文件：** `mewcode/prompt/builder.py`；新建 `tests/test_skills_builder.py`
**依赖：** 无（可与 T1-T4 并行）
**步骤：**
1. `SystemPromptBuilder.__init__` 增 `skills_summary: str = ""`、`skills_loader: Callable[[], str] | None = None`
2. 构造时组装 stable：`STABLE_TEXT + 【技能】块`（skills_summary 非空才加；格式 `【技能】\n- name: description`）
3. `build()` dynamic 顺序：补充块 → skills_loader 现取的非空块 → 记忆块 → 自定义指令块
**验证：** `python -m pytest tests/test_skills_builder.py tests/test_prompt.py -q` 全过

## T6: Agent 白名单收窄与执行拦截

**文件：** `mewcode/agent/loop.py`；新建 `tests/test_skills_agent.py`
**依赖：** T3、T5
**步骤：**
1. `Agent.__init__` 增 `skills_summary: str = ""`、`skill_manager: SkillManager | None = None`；builder 传 `skills_summary` 与 `skills_loader=skill_manager.active_snapshot`
2. `_visible_tools()`：计划模式只读过滤后，若 `skill_manager.whitelist_active()`：`allowed = whitelist() | {"use_skill"}`，仅保留 `t.name in allowed` 的工具
3. 执行预检：`self._registry.get(call.name)` 成功后、`gate.check` 前：有白名单收窄且 `call.name` 不在 allowed 且非 use_skill → `results[id(call)] = ToolResult.failure(f"技能白名单未包含该工具: {call.name}")`，continue
**验证：** `python -m pytest tests/test_skills_agent.py tests/test_agent_loop.py -q` 全过

## T7: 命令层三处修改与 /review 迁移

**文件：** `mewcode/commands/registry.py`、`mewcode/commands/builtins.py`、`mewcode/commands/ui.py`；新建 `tests/test_skills_commands.py`；修改既有命令测试
**依赖：** T3
**步骤：**
1. `registry.py`：增 `unregister(name)`（移除该命令 + `_indexed=False`；不存在静默忽略）；register/unregister/`_ensure_index` 加 `threading.Lock`（技能命令在工具执行线程注册，与主线程补全/分发并发）
2. `builtins.py`：删 `_cmd_review`、`REVIEW_PROMPT` 与注册条目（12→11 条）
3. `ui.py`：UiController 增 `run_skill(self, name: str, args: str) -> None`
4. 更新既有测试：/review 相关断言改为未知命令或删除、命令计数 12→11（`grep -rn "review" tests/` 逐个处理）
**验证：** `python -m pytest tests/test_skills_commands.py tests/test_command_*.py tests/test_plan_mode.py -q` 全过

## T8: 内置样板技能与打包

**文件：** `mewcode/skills/commit.md`、`review.md`、`test/SKILL.md`、`test/references/pytest-notes.md`；修改 `pyproject.toml`
**依赖：** T1
**步骤：**
1. `commit.md`：frontmatter（`name: commit`、description、`mode: independent`、`tools: [run_command]`、`history: 0`）；正文 SOP：git status/diff 查看改动 → 生成规范中文提交信息 → git add + commit → 报告结果
2. `review.md`：frontmatter（`name: review`、description、`mode: shared`、`tools: [run_command, read_file, find_files, grep_code]`）；正文 = ch12 `REVIEW_PROMPT` 原提示词
3. `test/SKILL.md`：目录型入口（`mode: shared`、tools 六件核心工具）；正文 SOP：跑 pytest → 分析失败 → 修复重跑（引用 references/pytest-notes.md）；`references/pytest-notes.md` 写 pytest 使用要点
4. `pyproject.toml` 增 `[tool.setuptools.package-data] mewcode = ["skills/**/*.md"]`
5. `tests/test_skills_discover.py` 增一条：`discover_skills(内置目录)` 发现 commit/review/test 三个样板且字段符合预期
**验证：** `python -m pytest tests/test_skills_discover.py -q` 全过（含内置样板断言）

## T9: CLI 装配

**文件：** `mewcode/cli.py`
**依赖：** T2、T4、T7、T8
**步骤：**
1. import discover_skills、validate_whitelists、skills_summary、SkillManager、UseSkillTool、BUILTIN_SKILLS_DIR
2. MCP 注册与 `gate.validate_tools` 之后：`discover_skills(project_root, USER_DIR, BUILTIN_SKILLS_DIR)`，警告逐条打 stderr
3. `manager = SkillManager(discovered.skills)`；`registry.register(UseSkillTool(manager))`
4. `validate_whitelists(discovered.skills, {t.name for t in registry.list()})` 非空 → stderr 输出全部问题 + `sys.exit(1)`
5. `run(..., skill_manager=manager, skills_summary=skills_summary(discovered.skills))`
**验证：** `python -m compileall mewcode/` 无错误；`python -m pytest -q` 全量通过

## T10: TUI 接入（独立执行 + 技能命令执行）

**文件：** `mewcode/tui/uicontroller.py`、`mewcode/tui/app.py`
**依赖：** T3、T6、T7
**步骤：**
1. `uicontroller.py`：`__init__` 增 `skill_manager: SkillManager | None`、`run_independent: Callable[[str, str], None] | None`；`run_skill(name, args)`：无 manager → display("技能系统未启用")；`activate` 失败 → display(error)；shared → display 激活提示 + `send_user_message(body)`；independent → `run_independent(name, args)`
2. `app.py run()`：增 `skill_manager: SkillManager | None = None`、`skills_summary: str = ""` 参数；agent 构造传入；manager 非 None 时 `attach_registry(command_registry)`
3. 定义 `_run_independent_skill(name, args)` 闭包：
   - `activate` 失败 → display 错误返回
   - `inner_session = ChatSession()`；`skill.history > 0` → `inner_session.restore(session.messages[-skill.history:])`
   - `inner_manager = SkillManager(manager.skills)`；`inner_manager.activate(name, args)`
   - `skill.model` 非空 → `create_provider(replace(config, model=skill.model))`，否则用原 provider
   - `inner_agent = Agent(inner_provider, registry, inner_session, custom_instructions=..., gate=gate, context_window=config.context_window, skills_summary=skills_summary, skill_manager=inner_manager, context_dir=独立子目录)`（context_dir 用 `root/.mewcode/context/skills`，避免与外层 ResultStore 序号撞车）
   - `end = _agent_round(console, inner_agent, args or f"请执行技能 {name}", tracker, gate, inner_session, log=None, updater=None, ui, prefix=...)`（不落会话存档；流式渲染与权限弹窗复用主循环）
   - 摘要：`end.reason is OK` → 内层新增消息中最后一条 assistant.text；否则失败说明文本；空摘要 → display 错误返回
   - 摘要面板展示 → `_run_agent_round(f"【技能 {name} 独立执行摘要】\n{summary}")` 自动续一轮
4. `_agent_round` 增 `prefix` 参数（缺省原「🐱 MewCode」前缀）+ 返回 `end`
5. 输入循环：`_run_agent_round` 返回后 `manager.take_pending()` → 有则 `_run_independent_skill(*pending)`
**验证：** `python -m compileall mewcode/` 无错误；`python -m pytest -q` 全量通过

## T11: e2e 冒烟

**文件：** `scripts/mock_llm.py`（增 skill-commit-model）
**依赖：** T10
**步骤：**
1. `mock_llm.py` 增 `skill-commit-model`：按请求内容分步——(a) 历史无 use_skill 结果且系统无【已激活技能】→ 调用 use_skill(commit)；(b) 历史含 use_skill 结果 → 文本「好的，正在执行 commit 技能。」结束本轮；(c) 系统含【已激活技能】且无工具结果 → 调用 run_command(git status)；(d) 系统含【已激活技能】且含 run_command 结果 → 文本「已完成提交：修复登录 bug。」；(e) 历史含【技能 commit 独立执行摘要】→ 文本汇报
2. tmux 启动 MewCode（config 指向 mock_llm，model=skill-commit-model），跑场景：
   - 场景 1：输入「帮我提交代码」→ 模型调 use_skill → 独立执行流式可见 → 摘要回流 → 自动续一轮汇报
   - 场景 2：激活后输入 `/commit 一些说明` 直接触发；`/help` 含 /commit；`/clear` 后 `/commit` 提示未知命令
   - 场景 3：检查 mock_requests.log 中激活后的请求 tools 字段只含白名单 ∪ use_skill
3. 修复发现的问题
**验证：** 三个场景输出符合预期（对照 checklist.md 逐项）

## 执行顺序

```
T1 → T2 → T3 → T4
              ↘
T5（可并行）→ T6 → T10 → T11
T7（依赖 T3）────────↗
T8（依赖 T1）────────→ T9 → T10
```

T5 可与 T1-T4 并行；T7、T8 在各自依赖完成后可并行。
