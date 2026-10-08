# Hook 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/hooks/__init__.py` | 包导出（HookManager/HookRule/HookDecision/load_hooks） |
| 新建 | `mewcode/hooks/loader.py` | 数据结构（HookCondition/HookAction/HookRule）+ YAML 解析与集中校验 |
| 新建 | `mewcode/hooks/matcher.py` | 条件引擎：get_path/match_pattern/eval_conditions |
| 新建 | `mewcode/hooks/actions.py` | ActionResult + 四动作同步执行 |
| 新建 | `mewcode/hooks/manager.py` | HookManager：分组/emit 双版本/run_once/inject 队列/日志 |
| 新建 | `tests/test_hooks_matcher.py` | 条件引擎测试 |
| 新建 | `tests/test_hooks_loader.py` | 加载校验测试 |
| 新建 | `tests/test_hooks_actions.py` | 动作执行测试 |
| 新建 | `tests/test_hooks_manager.py` | 管理器测试 |
| 新建 | `tests/test_hooks_agent.py` | loop 接入测试 |
| 修改 | `mewcode/agent/loop.py` | Agent 接入：hook_manager 参数、6 节点 emit、inject 队列、_finish |
| 修改 | `mewcode/tui/app.py` | run() 增 hook_manager 参数；session_start/end；独立技能路径同 |
| 修改 | `mewcode/cli.py` | load_hooks + HookManager 装配；startup/shutdown emit |

## T1: hooks 包骨架 + loader 数据结构

**文件：** `mewcode/hooks/__init__.py`、`mewcode/hooks/loader.py`
**依赖：** 无
**步骤：**
1. 建 `mewcode/hooks/__init__.py`：先放模块 docstring（「Hook 系统：生命周期事件上的自动化动作」），导出最后再补
2. `loader.py` 定义三个 dataclass（字段见 plan.md 核心数据结构）：`HookCondition(field, pattern)`、`HookAction(type, run="", prompt="", url="", method="GET", headers, body="")`、`HookRule(index, event, conditions, match, action, run_once=False, background=False, timeout=30.0)`
3. 定义常量：`HOOK_EVENTS`（10 事件名元组）、`INTERCEPT_EVENTS = {"tool_before"}`、`EVENT_FIELDS`（事件→允许顶层字段段映射，见 plan 字段表）、`ACTION_TYPES = {"command", "inject", "http", "subagent"}`、`HTTP_METHODS = {"GET", "POST", "PUT", "DELETE"}`、`DEFAULT_TIMEOUT = 30.0`

**验证：** `python -m compileall mewcode/hooks/` 无报错

## T2: matcher.py 条件引擎

**文件：** `mewcode/hooks/matcher.py`
**依赖：** T1（从 loader import HookCondition/HookRule）
**步骤：**
1. `MISSING` 哨兵对象；`get_path(ctx: dict, field: str)`：按 `.` 逐段查 dict，中途非 dict 或缺失返回 MISSING
2. `match_pattern(pattern, value) -> bool` 判定顺序：① `!` 前缀→剥一层递归取反；② `/` 开头且 `/` 结尾且长度 ≥2→`re.fullmatch(pattern[1:-1], value)`；③ 含 `*`/`?`→glob（`**/`→`*`、`**`→`*` 归一后 fnmatch，注释注明与 permissions/rules 口径一致）；④ 精确相等。value 非字符串先 `str()`
3. `eval_conditions(conditions, match, ctx) -> bool`：条件为空恒 True；`match="all"` 全真、`match="any"` 有真；get_path 得 MISSING → 该条件 False

**验证：** `python -m compileall mewcode/hooks/` 无报错

## T3: test_hooks_matcher.py

**文件：** `tests/test_hooks_matcher.py`
**依赖：** T2
**步骤：**
1. 断言四模式：精确（`"git status"` 命中 `"git status"`、不命中 `"git status -s"`）；glob（`"*.md"` 命中 `"a.md"`、`"**/x"` 命中 `"a/b/x"`）；`!`（`"!/tmp/*"` 不命中 `"/tmp/a"`、命中 `"/home/a"`）；正则（`"/^rm -/"` 命中 `"rm -rf /"`）
2. 断言 `!` 剥一层（`"!!git"` 语义=取反一次）
3. 点路径：`{"args": {"command": "x"}}` 取 `args.command` 命中；缺失/中途非 dict → 条件 False
4. all/any：两真/一真一假/两假的组合；条件为空列表恒 True
5. 非字符串值（bool/int）先 str() 再匹配

**验证：** `python -m pytest tests/test_hooks_matcher.py -q` 全过

## T4: actions.py 的 ActionResult + inject/subagent/command

**文件：** `mewcode/hooks/actions.py`
**依赖：** T1（HookAction）
**步骤：**
1. `ActionResult(ok, message, deny=False)` dataclass；`_truncate(text, limit=1000)` 截断
2. `exec_inject(action) -> ActionResult`：恒 ok，message=action.prompt
3. `exec_subagent(action) -> ActionResult`：日志 info「子 Agent 动作占位」后恒 ok
4. `exec_command(action, ctx, timeout) -> ActionResult`：`subprocess.run(action.run, shell=True, capture_output=True, text=True, timeout=timeout, input=json.dumps(ctx, ensure_ascii=False))`；env 在 `os.environ` 基础上加 `MEW_EVENT=ctx["event"]` 与 `MEW_TOOL`（ctx 有 tool 时）；exit 0→ok=True、message=stdout 截断；exit 非 0→ok=False、deny=True、message=stdout 截断；`TimeoutExpired`→ok=False、message="命令超时（N 秒）"

**验证：** `python -m compileall mewcode/hooks/` 无报错

## T5: exec_http + test_hooks_actions.py

**文件：** `mewcode/hooks/actions.py`（续写）、`tests/test_hooks_actions.py`
**依赖：** T4
**步骤：**
1. `exec_http(action, timeout) -> ActionResult`：`httpx.Client(timeout=timeout)` 同步 request（method/url/headers/body）；2xx→ok=True、message=状态摘要；非 2xx→ok=True、deny=True、message=响应体截断；`httpx.HTTPError`/超时→ok=False、message=异常信息
2. 测试文件：
   - command：`run: "python -c 'import json,sys; d=json.load(sys.stdin); print(d[\"event\"])'"` 断言 stdout=事件名（stdin JSON 正确）；环境变量 MEW_EVENT/MEW_TOOL 正确；`exit 1` 的脚本→ok=False、deny=True、message 含 stdout；`sleep 5` + timeout=1→ok=False
   - http：用 `httpx.MockTransport` 返回 200/404/抛异常，断言 ok/deny/message；url/headers/body 正确透传
   - inject/subagent：恒 ok；subagent 触发日志（caplog）

**验证：** `python -m pytest tests/test_hooks_actions.py -q` 全过

## T6: loader.py 的 parse_hooks/load_hooks

**文件：** `mewcode/hooks/loader.py`（续写）
**依赖：** T1、T2
**步骤：**
1. `parse_hooks(data: dict) -> list[HookRule]`：顶层无 `hooks` 键或空列表→`[]`；非列表→ConfigError
2. 逐条校验（错误信息格式 `Hook 规则第 N 条: ...`，全部中文）：
   - `event` 在 HOOK_EVENTS；`if` 缺失→无条件；`if` 含 `all` 或 `any` 键（二者都有或空映射→报错）→非空条件列表、不嵌套；否则 `if` 本身是单条条件映射
   - 条件：`field` 首段在 EVENT_FIELDS[event]；`pattern` 非空字符串；`/.../` 包裹时 `re.compile` 成功
   - `action`：`type` 在 ACTION_TYPES；command→`run` 非空字符串；inject/subagent→`prompt` 非空字符串；http→`url` 以 http:// 或 https:// 开头、`method` 在 HTTP_METHODS、`headers` 为字符串键值映射、`body` 字符串
   - 顶层：`run_once`/`background` 布尔；`background=true` 且 event 在 INTERCEPT_EVENTS→报错；`timeout` 为正数
   - 构造 HookRule：conditions 为空时 match=""、单条条件 match="all"（语义等价）、组合取 all/any
3. `load_hooks(path) -> list[HookRule]`：文件缺失→`[]`；yaml.safe_load 失败→ConfigError（格式错误）；非 dict→`[]`（与现有 custom_instructions 口径一致）；转 parse_hooks

**验证：** `python -m compileall mewcode/hooks/` 无报错

## T7: test_hooks_loader.py

**文件：** `tests/test_hooks_loader.py`
**依赖：** T6
**步骤：**
1. 合法样例：四种动作各一条的 hooks 列表解析成功，字段与缺省值正确（timeout 缺省 30、method 缺省 GET、run_once 缺省 False）
2. 单条条件映射（无 all/any）解析为 match="all"；无条件规则 conditions 为空
3. 非法样例各断言 ConfigError 且信息含「第 N 条」：非法 event、`/[/` 坏正则、if 同时含 all 和 any、all 为空列表、field 顶层段越界（如 session_start 上用 tool）、command 缺 run、http 的 url 非 http 前缀、http 的 method 非法、tool_before+background、timeout 为 0/负数/字符串、run_once 非布尔
4. 顶层无 hooks 键→`[]`；hooks 空列表→`[]`；`load_hooks` 文件缺失→`[]`；YAML 语法错误→ConfigError

**验证：** `python -m pytest tests/test_hooks_loader.py -q` 全过

## T8: manager.py HookManager

**文件：** `mewcode/hooks/manager.py`
**依赖：** T4、T6（数据结构与动作）、T2（条件）
**步骤：**
1. `HookDecision(allowed=True, reason="")` dataclass；`logger = logging.getLogger("mewcode.hooks")`
2. `__init__(rules)`：`_by_event: dict[str, list[HookRule]]` 分组；`_executed_once: set[int]`；`_pending_injects: list[str]`
3. `_run_rule_sync(rule, ctx) -> tuple[bool, str]` 核心：条件求值 False→日志 debug「跳过」返回 (True, "")；命中→按 action.type 分发执行；inject→message 入 `_pending_injects` 返回 (True, "")；其余→(result.ok, result.message)，deny 时 allowed=False 语义由调用方按事件决定
4. `emit(event, ctx) -> HookDecision`（同步版）：取 `_by_event.get(event, [])` 空→立即返回放行；逐条：run_once 且已执行→跳过；background→`threading.Thread(daemon=True)` 跑执行（只记日志不参与决策）；同步路径：拦截事件（tool_before）上 deny→allowed=False/reason、ok=False→fail-closed（allowed=False、reason=失败信息）；非拦截事件只记日志（deny→warning）不改变决策；命中即 `_executed_once.add(rule.index)`
5. `emit_async(event, ctx)`：与 emit 同核心；同步动作 `await asyncio.to_thread(...)`；background 走 `asyncio.create_task(asyncio.to_thread(...))`；拦截事件禁止 background 由 loader 保证
6. `take_injects()`：返回 `_pending_injects` 副本并清空
7. 触发日志：info 含事件名与规则序号；失败日志：warning

**验证：** `python -m compileall mewcode/hooks/` 无报错

## T9: test_hooks_manager.py

**文件：** `tests/test_hooks_manager.py`
**依赖：** T8
**步骤：**
1. 空规则/该事件无规则：emit 返回放行决策且动作不执行
2. 无条件规则：对应事件触发动作（命令写临时文件断言）；条件不命中→动作不执行、debug 日志
3. 同事件多规则：全部执行不短路（两条命令各写一个文件）；配置顺序执行
4. run_once：两次 emit 同一事件只执行一次（计数文件）
5. background：慢命令（sleep 1）background 规则 emit 立即返回（耗时断言 <0.5s）；异步版同样
6. 拦截：tool_before + `exit 1` 命令→allowed=False、reason=stdout；tool_before + 动作异常（不存在的命令/超时）→fail-closed allowed=False
7. 非拦截事件（round_start）上 `exit 1` 命令→allowed=True（仅 warning 日志）
8. take_injects：inject 动作后取走文本、二次取为空；caplog 断言触发/失败日志内容含事件名与序号

**验证：** `python -m pytest tests/test_hooks_manager.py -q` 全过

## T10: loop.py 接入

**文件：** `mewcode/agent/loop.py`
**依赖：** T8
**步骤：**
1. `__init__` 增 `hook_manager: HookManager | None = None` 参数；`self._hooks = hook_manager`；`self._round_count = 0`
2. `run()` 开头（`self._cancelled.clear()` 后）：`_round_count += 1`；若 `_hooks` 非 None：`await _hooks.emit_async("round_start", {"event": "round_start", "round": _round_count})`
3. `add_user` 后：`emit_async("user_message", {...})`；`add_assistant` 落盘后：`emit_async("assistant_message", {...})`
4. 每轮 build 前：`injects = _hooks.take_injects() if _hooks else []`；与既有 directives 合并——每个注入文本包装 `SwitchDirective(name="hook_inject", full_text=t, slim_text=t, repeat_every=1)`，追加进 directives 列表
5. 预检循环：闸门放行分支（`planned.append(call)` 前）加 tool_before——`decision = await _hooks.emit_async("tool_before", {"event": "tool_before", "round": _round_count, "tool": call.name, "args": call.args})`；`not decision.allowed` → `results[id(call)] = ToolResult.failure(decision.reason)` 并 continue（不进 planned）
6. 回灌循环（`yield ToolResultEvent(call, result)` 后）：`emit_async("tool_after", {"event": "tool_after", "round": _round_count, "tool": call.name, "args": call.args, "result": {"ok": result.ok, "content": result.content}})`
7. `_finish(self, reason: RoundEndReason, message: str = "")` 私有方法：emit round_end（含 reason.value）→ `yield RoundEnd(reason, message)`；五个停止路径（OK/LIMIT/UNKNOWN_TOOL/CANCELLED/ERROR）全部改走 `_finish`
8. `_hooks is None` 时所有 emit 调用跳过（零开销）

**验证：** `python -m pytest -q` 全量（ch14 存量测试不破）

## T11: test_hooks_agent.py

**文件：** `tests/test_hooks_agent.py`
**依赖：** T10
**步骤：**
1. 参照现有 mock provider 测试基建构造驱动 run() 的 mock（一轮流式回复 + 一次工具调用）
2. 事件时机：挂 10 个事件的命令规则（各写标记文件），跑一次含工具调用的会话，断言 round_start/user_message/assistant_message/tool_before/tool_after/round_end 各触发且 round 计数正确（第二次 run() 时 round=2）
3. 拦截：tool_before + 命令 `exit 1` + stdout「危险参数」→断言工具未执行（无执行副作用）、`ToolResultEvent` 的 result 为失败且 content 含「危险参数」、会话记录该失败结果
4. inject：tool_after 挂 inject 规则→断言下一次请求的 system.dynamic 含注入文本；该轮结束后再触发一次请求（若无）则文本不重复出现（全取全清）
5. hook_manager=None：行为与接入前一致（至少断言 run() 正常产出事件流）

**验证：** `python -m pytest tests/test_hooks_agent.py -q` 全过

## T12: cli.py + tui/app.py 装配

**文件：** `mewcode/cli.py`、`mewcode/tui/app.py`
**依赖：** T8
**步骤：**
1. `cli.py`：`main` 中 load_config 之后 `load_hooks(args.config)`（同路径）→ `HookManager(hook_rules)`（无规则也构造——空管理器零开销）；`emit("startup", {"event": "startup"})` 装配完成后；main 末尾（finally 块）`emit("shutdown", {"event": "shutdown"})`；hook_manager 传给 `tui.run(...)`
2. `tui/app.py`：`run()` 增 `hook_manager: HookManager | None = None` 参数（缺省 None 向后兼容）；session 创建后 `emit("session_start", {"event": "session_start"})`；主循环退出前 `emit("session_end", {"event": "session_end"})`（含 Ctrl+C 双击退出与异常退出路径）
3. `_run_independent_skill`：inner_session 创建后/结束前同样 emit session_start/session_end；inner_agent 构造时传 hook_manager
4. `Agent(...)` 构造处传 `hook_manager=hook_manager`

**验证：** `python -m pytest -q` 全量通过 + `python -m compileall mewcode/` 无报错

## T13: 全量回归

**依赖：** T12
**步骤：**
1. `python -m pytest -q` 全量通过（ch14 存量 593 例 + 新增 hook 用例）
2. `python -m mewcode --help` 正常输出（cli 装配未破坏启动路径）
3. 提交代码（本任务与 T12 可合并提交）

**验证：** 全量测试通过、--help 正常

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13
```
（线性执行；T3 与 T4-T5 无相互依赖但保持线性便于提交追踪）

**提交点：** T3 后（matcher+测试）、T5 后（actions+测试）、T7 后（loader+测试）、T9 后（manager+测试）、T11 后（loop 接入+测试）、T13 后（装配+回归）
