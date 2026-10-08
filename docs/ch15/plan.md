# Hook 系统 Plan

## 架构概览

新包 `mewcode/hooks/`，四个模块 + 三处接入点：

- **loader.py**：从 config.yaml 顶层 `hooks:` 解析并集中校验（F8）。解析与校验分离（`parse_hooks(data)` 纯函数便于测试），沿用 `ConfigError` 中文报错口径
- **matcher.py**：条件引擎（F3）——单串四模式判定 + 点路径字段取值 + all/any 组合求值。独立实现、不依赖 permissions 包（glob 归一逻辑复制三行并注释口径一致，permissions 零改动）
- **actions.py**：四种动作的同步执行函数（F5）——command（subprocess + stdin JSON + 环境变量 + 超时）、http（httpx 同步，已有依赖）、inject（返回文本由 manager 入队）、subagent（占位：日志后返回成功）
- **manager.py**：HookManager（F4/F6/F7）——按事件分组索引、emit 流程（过滤→条件→执行→决策）、run_once 标记、background 调度、失败日志、注入队列

接入点：
- `agent/loop.py`：Agent 增 `hook_manager` 参数（缺省 None=零开销）；轮次/消息/工具级 6 个节点 emit；tool_before 拒绝复用 `ToolResult.failure(reason)` 回灌；inject 文本走 SwitchDirective 通道进下一次请求
- `tui/app.py`：会话级 session_start/session_end 在会话创建/主循环退出处 emit（含独立技能路径）
- `cli.py`：startup/shutdown 在装配后/main 返回前 emit（同步版）

## 核心数据结构

```python
# loader/matcher 共用
@dataclass
class HookCondition:
    field: str      # 点路径："tool"、"args.command"、"result.ok"
    pattern: str    # 单串四模式："git status" / "*.md" / "/^rm /" / "!/tmp/*"

@dataclass
class HookAction:
    type: str                 # "command" | "inject" | "http" | "subagent"
    run: str = ""             # command 的命令串
    prompt: str = ""          # inject 的静态文本 / subagent 占位提示
    url: str = ""             # http 的地址（仅 http/https）
    method: str = "GET"       # GET/POST/PUT/DELETE
    headers: dict = field(default_factory=dict)
    body: str = ""            # http 请求体

@dataclass
class HookRule:
    index: int                # 配置序号（1 起，日志与报错定位）
    event: str                # 10 事件名之一
    conditions: list[HookCondition]
    match: str                # "all" | "any"（条件为空时恒 True）
    action: HookAction
    run_once: bool = False
    background: bool = False
    timeout: float = 30.0     # 秒；仅 command/http 生效
```

```python
# 动作执行结果与 emit 决策
@dataclass
class ActionResult:
    ok: bool                  # 动作自身成功与否
    message: str              # stdout / 响应摘要 / 错误信息（截断上限 1000 字符）
    deny: bool = False        # 非零退出 / 非 2xx → 拦截类事件拒绝

@dataclass
class HookDecision:
    allowed: bool = True      # tool_before 拒绝时为 False
    reason: str = ""          # 拒绝原因（回灌给模型）
```

```python
class HookManager:
    def __init__(self, rules: list[HookRule]) -> None
        # 按事件分组索引 rules_by_event；run_once 已执行集
    def emit(self, event: str, ctx: dict) -> HookDecision
        # 同步版：装配层用（startup/shutdown/session 级）；background 走 daemon 线程
    async def emit_async(self, event: str, ctx: dict) -> HookDecision
        # 异步版：主循环内用；同步动作 await asyncio.to_thread，
        # background 走 asyncio.create_task；拦截类事件强制同步等待
    def take_injects(self) -> list[str]
        # Agent 每轮 build 前取走待注入提示词（全取全清）
```

### 事件上下文字段表（emit 的 ctx 内容与 if 允许字段）

| 事件 | ctx 字段 | 条件允许的 field 顶层段 |
|------|---------|------------------------|
| startup / shutdown | event | event |
| session_start / session_end | event | event |
| round_start | event, round | event, round |
| round_end | event, round, reason | event, round, reason |
| user_message / assistant_message | event, round | event, round |
| tool_before | event, round, tool, args | event, round, tool, args.* |
| tool_after | event, round, tool, args, result(ok/content) | 同左，另 result.ok / result.content |

`args.*` 任意后续段由运行时点路径判定；`round` 为本次会话的用户输入轮计数（Agent 内部自增，run() 每次 +1，独立技能路径独立计数）；消息级事件 ctx 不含消息全文（stdin JSON 只传元信息）。

## 模块设计

### loader.py
**职责：** YAML hooks 列表解析与集中校验（F1/F8）
**对外接口：** `load_hooks(path) -> list[HookRule]`（文件缺失、hooks 缺失或空列表 → `[]`）；`parse_hooks(data: dict) -> list[HookRule]`（已解析数据校验，测试友好）
**校验清单**（任一非法抛 ConfigError，消息含规则序号）：
- `event` 必须是 10 事件名之一；`if` 结构：单条条件映射 或 含 `all`/`any` 键之一（二选一、非空列表）；`all`/`any` 不嵌套
- 条件 `field` 顶层段在该事件允许集合内（见字段表）；`pattern` 非空字符串；`/.../` 包裹的 pattern 必须能编译（`re.compile` 失败即报错）
- `action.type` 四值之一；command 须有非空 `run`；inject/subagent 须有非空 `prompt`；http 须有 `url`（http/https 开头）、method 白名单、headers 为字符串映射
- `tool_before` 配 `background: true` → 报错；`timeout` 非正数 → 报错；`run_once`/`background` 非布尔 → 报错
**依赖：** mewcode.config.ConfigError、yaml

### matcher.py
**职责：** 条件引擎（F3）
**对外接口：** `get_path(ctx, field) -> object`（点路径逐段查 dict，缺失返回 MISSING 哨兵）；`match_pattern(pattern, value) -> bool`（`!` 前缀剥一层取反 → `/.../` 正则 fullmatch → 含 `*`/`?` glob（`**/`→`*`、`**`→`*` 归一，注释注明与权限规则口径一致）→ 精确相等；value 非字符串先 str()）；`eval_conditions(rule, ctx) -> bool`（任一字段缺失 → 该条件 False；all = 全真、any = 有真；无条件恒 True）
**依赖：** 无（标准库 re/fnmatch）

### actions.py
**职责：** 动作的同步执行（F5/F6 的 timeout）；全部阻塞函数，由 manager 决定放线程/任务
**对外接口：**
- `exec_command(action, ctx, timeout) -> ActionResult`：`subprocess.run(action.run, shell=True, input=ctx JSON, timeout)`；环境变量加 `MEW_EVENT`（事件名）与 `MEW_TOOL`（工具级有 tool 时）；exit 0 → ok，stdout 为 message；exit 非 0 → ok=False, deny=True；TimeoutExpired → ok=False（不 deny，走 F4 拦截失败 fail-closed）
- `exec_http(action, timeout) -> ActionResult`：httpx 同步请求；2xx → ok；非 2xx → ok=True 但 deny=True（拦截语义按状态码），message 取响应体截断；网络异常/超时 → ok=False（拦截失败 fail-closed）
- `exec_inject(action) -> ActionResult`：恒 ok，message=注入文本（manager 入队）
- `exec_subagent(action) -> ActionResult`：恒 ok，日志「子 Agent 动作占位」后返回
**依赖：** httpx、subprocess、json

### manager.py
**职责：** emit 流程编排、run_once、background 调度、注入队列、失败日志（F4/F6/F7）
**emit 流程**（emit/emit_async 共用核心，仅调度差异）：
1. 取该事件规则列表（空 → 立即返回放行决策，零开销）
2. 逐条（配置顺序）：run_once 且已执行过 → 跳过；条件求值 False → 记「跳过」日志并继续
3. 命中 → 执行动作：inject 动作文本入队（inject 无 deny）；其余动作按 background 分支调度；同步路径拿 ActionResult
4. 拦截类事件（tool_before）上：`deny=True` → 决策 allowed=False, reason=message；`ok=False` → fail-closed：allowed=False, reason=失败信息；其余事件上 ok/deny 只记日志（deny 在非拦截事件无拒绝语义，仅警告日志）
5. 非拦截类事件多规则时：逐条全执行，不短路（副作用都要跑）
**日志：** `logging.getLogger("mewcode.hooks")`，触发（info，含事件名与规则序号）、跳过（debug）、失败（warning）——跟随 mcp 包先例，不引入新设施；日志只增不减
**依赖：** matcher、actions、logging、asyncio、threading

## 模块交互

```
启动  cli.main:
  load_hooks(path) → [HookRule] → HookManager → emit("startup", {event})
  → 装配 agent/tui → tui.run(...)
退出  main 末尾（finally）: emit("shutdown", {event})

会话  tui.run: session 创建后 emit("session_start")
  → prompt 输入循环 → Agent.run(user_text) ...
  → 主循环退出前 emit("session_end")（独立技能路径 inner_session 同）

轮次  Agent.run: _round_count += 1 → emit_async("round_start", {event, round})
  → [迭代循环]
  → 每个停止路径经 _finish(reason): emit_async("round_end", {event, round, reason}) → yield RoundEnd

消息  加用户消息后 emit_async("user_message", {event, round})
  助手落盘后 emit_async("assistant_message", {event, round})

工具  预检循环（计划模式/白名单/闸门全部放行后）:
  decision = await emit_async("tool_before", {event, round, tool, args})
  → 拒绝: results[id] = ToolResult.failure(decision.reason)，不执行
  执行完成回灌后: emit_async("tool_after", {event, round, tool, args, result})

注入  manager.take_injects() 由 Agent 每轮 build 前调用 → 包装成一次性
  SwitchDirective(full=slim=文本, repeat_every=1) → build(directives, iteration)
  → 请求发出即清空（无下一次请求则文本自然丢弃）
```

## 文件组织

```
mewcode/hooks/                  （新包）
├── __init__.py    — 导出 HookManager/HookRule/HookDecision/load_hooks
├── loader.py      — parse_hooks/load_hooks：YAML 解析与集中校验（F1/F8）
├── matcher.py     — get_path/match_pattern/eval_conditions（F3）
├── actions.py     — 四动作同步执行函数 + ActionResult（F5/F6 超时）
└── manager.py     — HookManager：分组/emit 双版本/run_once/inject 队列/日志（F4/F6/F7）

修改：
├── mewcode/agent/loop.py  — Agent 增 hook_manager 参数（None=零开销）；
│    _round_count 轮计数；6 个 emit_async 接入点；_finish 统一收尾；
│    inject 队列接入 build；tool_before 拒绝回灌
├── mewcode/tui/app.py     — run() 增 hook_manager 参数；session_start/end emit；
│    _run_independent_skill 同
└── mewcode/cli.py         — load_hooks + HookManager 装配；startup/shutdown emit

tests/                         （新）
├── test_hooks_matcher.py  — 四模式、!递归一层、点路径缺失、all/any、非字符串值
├── test_hooks_loader.py   — 合法样例、非法 event/正则/背景冲突/timeout、错误信息含序号
├── test_hooks_actions.py  — command stdin JSON/env/退出码/超时；http 2xx/非2xx/异常；inject；subagent 占位
├── test_hooks_manager.py  — 分组零开销、顺序执行、run_once、background 不阻塞、拦截决策、失败隔离
└── test_hooks_agent.py    — mock provider 驱动 run()：各事件时机、拦截回灌含原因、
                             inject 出现在下一次请求且请求后消失
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Hook 引擎位置 | HookManager 作为 Agent 协作者，loop 内显式 emit | 拦截回灌与注入时机必须在主循环内；事件流驱动（TUI 订阅）无法回灌、独立技能路径不生效 |
| 双 emit 版本 | `emit`（同步）+ `emit_async`（异步）共享核心 | 装配层（cli/tui）是同步代码，主循环是 asyncio；同步动作 30s 上限不能阻塞事件循环，async 版经 to_thread |
| inject 通道 | 复用 SwitchDirective：包装一次性 directive 进 build | builder 零改动；「下一次请求后移除」由 take_injects 全取全清天然实现 |
| 匹配实现 | hooks/matcher.py 独立实现，复制 glob 归一三行 | hooks 不依赖 permissions（方向干净）；归一逻辑注释注明口径一致，权限规则零改动 |
| 命令执行 | `subprocess.run(shell=True)` + stdin 传 JSON | 规则来自用户自己的配置文件，信任边界=用户本人；shell=True 让命令写法自然 |
| HTTP 客户端 | httpx 同步 client + timeout | 项目已有 httpx 依赖，零新增 |
| 拦截动作失败 | fail-closed：拒绝该调用 | 安全默认；且拒绝原因回灌后模型可调整，不中断主流程 |
| 日志设施 | `logging.getLogger("mewcode.hooks")` | 跟随 mcp 包先例；不引入新日志框架；stderr 输出不干扰 TUI |
| background 生命周期 | daemon 线程 / 不 await 的 task，进程退出即弃 | 本步不做收尾等待；失败仍记日志 |
| round 计数归属 | Agent 内部自增（run() 每次 +1），独立技能路径独立计数 | 轮次语义=用户输入轮，Agent 持有最自然；inner_agent 是独立 Agent 实例 |
| 拒绝原因长度 | message 截断 1000 字符 | 防超长 stdout/响应体撑爆回灌内容 |
| timeout 与动作绑定 | 仅 command/http 生效；inject/subagent 上出现该字段被忽略 | F8 校验清单未列该冲突，不做过度校验 |
