# ch12 斜杠命令系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/commands/ui.py` | UiController Protocol + 6 个查询数据类 |
| 新建 | `mewcode/commands/parser.py` | ParsedCommand / parse_command |
| 新建 | `mewcode/commands/registry.py` | CommandType / CommandSpec / CommandRegistry / CommandConflictError |
| 新建 | `mewcode/commands/dispatch.py` | CommandResult / dispatch |
| 新建 | `mewcode/commands/builtins.py` | 12 条内置命令、/help 渲染、build_registry |
| 新建 | `mewcode/commands/__init__.py` | 包导出 |
| 新建 | `tests/test_command_parser.py` | 解析矩阵单测 |
| 新建 | `tests/test_command_registry.py` | 注册/冲突/别名/visible 单测 |
| 新建 | `tests/test_command_dispatch.py` | 分发单测 |
| 新建 | `tests/test_command_builtins.py` | 12 条 handler 单测（FakeUiController） |
| 新建 | `tests/test_command_completion.py` | 补全矩阵单测 |
| 修改 | `mewcode/permissions/rules.py` | RuleSet 增 counts() |
| 修改 | `tests/test_rules.py` | counts() 用例 |
| 新建 | `mewcode/tui/uicontroller.py` | TuiUiController（接口实现 facade） |
| 新建 | `mewcode/tui/completion.py` | CommandCompleter |
| 修改 | `mewcode/tui/app.py` | 装配/状态栏/分流器/record_usage/删 _resume_flow |
| 修改 | `mewcode/cli.py` | build_registry + 冲突退出 |
| 删除 | `mewcode/tui/commands.py` | 迁入 commands/ 包 |

## T1: 界面控制接口与查询数据类

**文件：** `mewcode/commands/ui.py`、`mewcode/commands/__init__.py`（先建空文件占位）
**依赖：** 无
**步骤：**
1. 新建 `mewcode/commands/` 包目录与空 `__init__.py`
2. 写 `ui.py`：7 个 dataclass（TokenStats、StatusInfo、SessionInfo、MemoryInfo、PermissionInfo、ResumeItem），字段与默认值严格按 plan.md「核心数据结构」
3. 定义 `UiController(Protocol)`：16 个方法签名按 plan.md（display、send_user_message、enter_plan_mode、exit_plan_mode、cycle_permission_mode、refresh_status、clear_conversation、compact_now、choose、status_info、session_info、memory_info、permission_info、list_sessions、restore_session）

**验证：** `python -m compileall mewcode/commands/` 通过；`python -c "from mewcode.commands.ui import UiController, StatusInfo"` 无报错

## T2: 斜杠解析器

**文件：** `mewcode/commands/parser.py`、`tests/test_command_parser.py`
**依赖：** T1
**步骤：**
1. `ParsedCommand` frozen dataclass（name、args）
2. `parse_command(text)`：空/纯空白 → None；不以 `/` 开头 → None；正则 `^/(\S*)(?:[ \t]+(.*))?$` 匹配，name 转小写，args 取原文（无参数时为空串）
3. 测试矩阵：`""`/`"   "` → None；`"你好"` → None；`"/help"` → (`help`, `""`)；`"/HELP  abc"` → (`help`, `" abc"`)；`"/plan "` → (`plan`, `""`)；`"/"` → (`""`, `""`)；`"/ x"` → (`""`, `"x"`)

**验证：** `python -m pytest tests/test_command_parser.py -q` 全过

## T3: 命令注册中心

**文件：** `mewcode/commands/registry.py`、`tests/test_command_registry.py`
**依赖：** T1、T2（类型标注）
**步骤：**
1. `CommandType` 枚举（LOCAL/UI/PROMPT）；`CommandSpec` frozen dataclass（handler 类型标注引用 `dispatch.CommandResult`，文件头 `from __future__ import annotations` 使字符串注解不要求 T4 已实现）
2. `CommandRegistry`：register（name/aliases 归一小写，有序列表存储）；validate（四类冲突：名撞名/名撞别名/别名撞名/别名撞别名，非法名：空/含空白；返回问题列表）；resolve（validate 后建索引，别名解析到主名）；visible（非 hidden、按名称排序）；all
3. `CommandConflictError(Exception)` 携带冲突详情文本
4. 测试：正常登记与 resolve（主名/别名/大小写）；四类冲突各自命中；非法名；visible 排除 hidden 且排序

**验证：** `python -m pytest tests/test_command_registry.py -q` 全过

## T4: 分发器

**文件：** `mewcode/commands/dispatch.py`、`tests/test_command_dispatch.py`
**依赖：** T3
**步骤：**
1. `CommandResult` 枚举（CONTINUE/EXIT）
2. `dispatch(registry, parsed, ui)`：resolve 未命中或无 handler → `ui.display("未知命令: /xxx（输入 /help 查看可用命令）")` 返回 CONTINUE；命中 → 调 handler 并透传结果，None 按 CONTINUE
3. 测试：未知命令文案含 /help；handler 被调用且参数正确；EXIT 透传；None 按 CONTINUE

**验证：** `python -m pytest tests/test_command_dispatch.py -q` 全过

## T5: 内置命令与注册构建

**文件：** `mewcode/commands/builtins.py`、`tests/test_command_builtins.py`
**依赖：** T4
**步骤：**
1. 测试内实现 `FakeUiController`：记录调用序列与各查询方法可配置返回值
2. `register_builtins(registry)`：登记 12 条（元数据按 plan「别名表」：/help→h、/exit→q/quit、/review→r、/memory→m、/permission→mode/perm、/session→s，其余无别名；类型：/help /status /session /memory=LOCAL，/review=PROMPT，其余=UI）
3. `build_registry()`：登记 → validate → 非空抛 `CommandConflictError`（详情文本拼接）
4. 12 个 handler 按 plan「各命令实现要点」：/help 动态渲染（列表含别名展示/单条详情/未知命令提示，registry 闭包捕获）；/plan //do 幂等提示；/permission show 分支；/review 用 `_REVIEW_PROMPT` 常量调 send_user_message；/resume 走 list_sessions→choose→restore_session 序列且空列表/取消分支；/exit 显示再见+EXIT
5. 测试：12 条 handler 行为矩阵（接口调用序列与文案断言）；/help 渲染含全部可见命令；build_registry 冲突抛异常

**验证：** `python -m pytest tests/test_command_builtins.py -q` 全过

## T6: 规则统计访问

**文件：** `mewcode/permissions/rules.py`、`tests/test_rules.py`
**依赖：** 无（可与 T2–T5 并行）
**步骤：**
1. `RuleSet.counts() -> tuple[int, int]`：返回（会话规则数 = `len(_rules) - len(_file_rules)`，永久规则数 = `len(_file_rules)`），带中文注释
2. tests/test_rules.py 增用例：空集合 (0,0)；文件规则计入永久；会话规则计入会话

**验证：** `python -m pytest tests/test_rules.py -q` 全过

## T7: TUI 界面控制器实现

**文件：** `mewcode/tui/uicontroller.py`
**依赖：** T1、T5、T6
**步骤：**
1. `TuiUiController`：构造注入 console/session/agent/gate/memory/config/root/`run_agent_round`（Callable）/`invalidate`（Callable）
2. 实现 16 个接口方法：display→`console.print(Text(text))`；send_user_message→`run_agent_round(text)`；模式切换→调 agent 对应方法 + `refresh_status()`；`cycle_permission_mode` 返回 `MODE_LABELS[新档位]`；`refresh_status`→`invalidate()`；clear_conversation→`session.clear()`；compact_now→`agent.compact_now()`
3. choose：面板展示 items（现有 `_resume_flow` 列表样式迁入）+ `prompt("选择编号（回车取消）> ")` 循环校验，返回 1 起编号或 None（EOF/空输入）
4. list_sessions：`memory.store.list_recent(5)` 转 ResumeItem（label = 标题+ID+消息数+时间）
5. restore_session：迁入现有 `_resume_flow` 恢复主体（load → restore → 时间跨度提醒 → log 换句柄 → maybe_compact_on_restore），返回汇总反馈文本；记忆未启用时由 handler 先拦截
6. status/session/memory/permission_info：按 plan「模块设计」组装（session_info 标题取首条用户消息截断；memory_info 数三层文件存在性/`*.md` 计数/`*.jsonl` 计数；permission_info 经 `gate 规则 counts()`；status_info 的 context_tokens = 消息总字符 // 4）
7. `record_usage(usage: dict)`：存最近一轮 + 累计（非接口方法）
8. 可提纯的格式化函数（status/session/memory 文案组装）提为模块级纯函数

**验证：** `python -m compileall mewcode/tui/` 通过；`python -c "from mewcode.tui.uicontroller import TuiUiController"` 无报错；`pytest tests/test_command_builtins.py -q` 不回退

## T8: Tab 补全器

**文件：** `mewcode/tui/completion.py`、`tests/test_command_completion.py`
**依赖：** T3
**步骤：**
1. `CommandCompleter(Completer)`：`get_completions`——输入不以 `/` 开头或含空格 → 不补；前缀小写匹配 `registry.visible()` 命令名或任一别名；单匹配 yield `Completion("/" + 主名, start_position=-len(text))`；多匹配逐项 yield（display_meta=描述）；主名与别名同时命中同一命令去重
2. 测试（用 prompt_toolkit `Document(text, cursor_position)` 构造）：`/cl` 单补 `/clear`；`/c` 菜单含 /clear /compact；`/mo` 补 `/permission`；`/m` 菜单含 /memory /permission；无斜杠/含空格不补；hidden 不参与；0 匹配空

**验证：** `python -m pytest tests/test_command_completion.py -q` 全过

## T9: TUI 装配与状态栏

**文件：** `mewcode/tui/app.py`
**依赖：** T7、T8
**步骤：**
1. `run(..., registry: CommandRegistry)` 增参；装配 `TuiUiController`（`run_agent_round` 用 `functools.partial` 绑定现有 `_agent_round` 参数；`invalidate` 在 PromptSession 创建后注入，注意构造顺序）
2. `completer=CommandCompleter(registry)` 替换 NestedCompleter（删除 `COMMAND_NAMES` 引用）
3. bottom_toolbar 改状态栏：`tracker.hint_active()` → 现有 Ctrl+C 提示；否则 `[STRICT]/[DEFAULT]/[ACCEPT]`（gate.mode.value 大写）+ plan_mode 时追加 `[PLAN]`
4. 欢迎面板「输入 /help 查看可用命令」保留

**验证：** `python -m compileall mewcode/tui/` 通过；`python -m pytest tests/test_thinking_indicator.py -q` 不回退

## T10: 回车入口分流器

**文件：** `mewcode/tui/app.py`
**依赖：** T9
**步骤：**
1. 主循环改为：`parsed = parse_command(text)`；None 且空白 → continue；None 且非斜杠 → `_agent_round(...)`；命令 → `dispatch(registry, parsed, ui)`，EXIT 则 return
2. `_agent_round` 轮末（正常与取消路径均）调用 `ui.record_usage(usage)`
3. 删除 `/resume` 特殊拦截分支与 `_resume_flow` 函数（已迁入 T7）
4. 删除旧 import：`handle_command`、`COMMAND_NAMES`、`CommandResult`（改从 `mewcode.commands` 导入）

**验证：** `python -m pytest tests/ -q` 全量通过（现有 441 例不回退）

## T11: CLI 启动接入

**文件：** `mewcode/cli.py`
**依赖：** T5
**步骤：**
1. import `build_registry`、`CommandConflictError`
2. 进入 TUI 前：try `build_registry()`；except → `print(f"命令注册冲突：\n{e}", file=sys.stderr)` + `sys.exit(1)`
3. `run(..., registry=registry)`

**验证：** `python -m compileall mewcode/cli.py` 通过；`python -m mewcode --help` 正常输出

## T12: 删除旧命令模块

**文件：** `mewcode/tui/commands.py`
**依赖：** T10、T11
**步骤：**
1. `grep -rn "tui.commands\|handle_command\|COMMAND_NAMES" mewcode/ tests/` 确认零引用
2. 删除文件
3. `mewcode/commands/__init__.py` 补全导出（CommandType、CommandSpec、CommandRegistry、CommandConflictError、parse_command、ParsedCommand、dispatch、CommandResult、UiController、build_registry、查询数据类）

**验证：** 上一步 grep 无结果；`python -m pytest tests/ -q` 全量通过；`python -c "import mewcode.commands"` 无报错

## T13: 全量回归

**文件：** 无（验证任务）
**依赖：** T1–T12
**步骤：**
1. `python -m pytest tests/ -q` 全量
2. `python -m compileall mewcode/`
3. 启动一次真实 TUI，输入 `/help`、`/status` 目测正常

**验证：** 单测全过；编译无错；/help 列出 12 条命令、/status 显示状态

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 ──────────┐
     T6（可并行）────────────────┤
T3 ─────────────→ T8 ────────────┤
T1 + T5 + T6 → T7 ───────────────┤
                                  ├→ T9 → T10 → T11 → T12 → T13
```
