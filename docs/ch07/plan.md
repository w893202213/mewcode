# 五层防御权限系统 Plan

## 架构概览

新增 `mewcode/permissions/` 包，五个组件；Agent Loop 在执行入口前接入一个集中闸门（方案 A）：

- **blacklist**（黑名单）：内置高危命令正则表，只查 `run_command` 的 command，命中即拒，无任何放开途径
- **sandbox**（路径沙箱）：项目根前缀判断（先 `resolve()` 解析符号链接），五个文件工具共用，硬边界
- **rules**（规则引擎）：Rule/RuleSet 数据结构，三 YAML 合并读取、精确+glob 匹配、deny>ask>allow 裁决、会话规则与永久写入
- **gate**（闸门）：按固定层序编排上述三层 + 模式兜底，输出 Verdict；只做纯计算不阻塞——ask 的暂停发生在 Agent 层
- **Agent Loop 接入**：执行前逐调用预检；ASK 裁决 → 产出 `PermissionAskEvent` 并等待 `answer_permission()`（TUI 主线程询问后应答）；DENY → 失败结果回灌不执行；拒绝永不终止循环

## 核心数据结构

```python
# permissions/modes.py（并入 gate.py 亦可，独立成类）
class Mode(Enum):
    STRICT = "strict"      # 未命中: 只读放行、副作用拒绝
    DEFAULT = "default"    # 未命中: 只读放行、副作用询问
    ACCEPT = "accept"      # 未命中: 一律放行

class Action(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

@dataclass
class Verdict:
    action: Action
    reason: str  # 中文说明；ALLOW 时可为空

# permissions/rules.py
@dataclass
class Rule:
    tool: str      # 工具名（六个之一）
    pattern: str   # 精确或 glob 模式
    action: Action

class RuleSet:
    def load_from(self, paths: list[Path]) -> None      # 三文件合并；缺失跳过、损坏抛 ConfigError
    def add(self, rule: Rule) -> None                    # 会话规则追加
    def match(self, tool: str, value: str) -> list[Action]  # 命中的全部规则裁决
    def verdict(self, tool: str, value: str, default: Action) -> Action
        # deny > ask > allow；无命中 → default
    def append_to_local_file(self, rule: Rule) -> None  # 永久允许：读改写本地级 YAML

# permissions/gate.py
class PermissionGate:
    def __init__(self, root: Path, rules: RuleSet, mode: Mode = Mode.DEFAULT)
    def check(self, tool: Tool, call: ToolCall) -> Verdict   # 纯计算：层序编排
    def grant_session(self, rule: Rule) -> None              # 本会话允许
    def grant_permanent(self, rule: Rule) -> None            # 永久允许（写本地级）
    def cycle_mode(self) -> Mode                             # strict→default→accept→strict

# permissions/blacklist.py
BLACKLIST: list[tuple[re.Pattern, str]]  # (正则, 中文描述)
def find_hit(command: str) -> str | None # 返回命中描述，未命中 None

# permissions/sandbox.py
def check_inside(root: Path, path: str) -> tuple[bool, str]  # (是否在根内, 原因)

# agent/events.py 新增
@dataclass
class PermissionAskEvent:
    call: ToolCall
    reason: str  # 触发询问的原因（ask 规则或默认询问）

# Agent（loop.py）新增
def __init__(self, ..., gate: PermissionGate | None = None)  # None → 默认闸门(根=cwd, default 档)
def answer_permission(self, allow: bool) -> None             # 线程安全应答
```

## 模块设计

### 模块 1：`permissions/blacklist.py`
**职责**：内置高危命令黑名单，唯一不可放开的层。
**对外接口**：`BLACKLIST`（(正则, 中文描述) 列表）、`find_hit(command) -> str | None`
**内置模式**（约 9 条，全部单元测试覆盖）：
| 正则（简化描述） | 拦什么 |
|---|---|
| `:\(\)\s*\{...\};` | fork bomb |
| `\bmkfs(\.\w+)?\b` | 格式化文件系统 |
| `\bdd\b` | dd 直写设备 |
| `\bsudo\b` | 提权 |
| `rm` + 递归/强制标志 + `/(\s\|\$\|\*)` | rm -rf / 与 rm -rf /*（`rm -rf build/` 不拦） |
| `(curl\|wget)...\|...(ba\|z\|fi)?sh` | 下载即执行 |
| `\b(shutdown\|reboot\|poweroff\|halt)\b` | 关机重启 |
| `>\s*/dev/(sd\|hd\|nvme\|mapper)` | 重定向覆写设备 |
| `chmod` + `-R` + `/` | 递归改根权限 |
**依赖**：无（标准库 re）

### 模块 2：`permissions/sandbox.py`
**职责**：五个文件工具的路径硬边界。
**对外接口**：`check_inside(root: Path, path: str) -> tuple[bool, str]`
**逻辑**：相对路径以 root 为基准拼接 → `Path.resolve(strict=False)` 解析全链符号链接 → `is_relative_to(root.resolve())` 前缀判断；root 本身在启动时 `resolve()` 一次。目标不存在（write_file 新建）时 resolve 按已存在部分解析，天然覆盖「解析父路径」。
**依赖**：pathlib

### 模块 3：`permissions/rules.py`
**职责**：规则的数据结构、匹配、三文件合并读写。
**对外接口**：`Rule`、`RuleSet`（load_from / add / match / verdict / append_to_local_file）
**逻辑**：
- 三个路径：用户级 `~/.mewcode/permissions.yaml`、项目级 `<root>/.mewcode/permissions.yaml`、本地级 `<root>/.mewcode/permissions.local.yaml`
- YAML 结构 `rules: [{tool, pattern, action}]`；tool 必须是六个工具名之一、action 必须是 allow/ask/deny，否则 ConfigError（fail-closed）；文件缺失跳过、损坏报错
- 匹配：先精确相等，再 fnmatch glob（`**` 归一为 `*`，`*` 跨路径分隔符匹配）；`match()` 返回所有命中规则的 Action
- `verdict(tool, value, default)`：有 deny → DENY；有 ask → ASK；有 allow → ALLOW；无命中 → default
- `append_to_local_file`：读本地级 YAML（缺失则新建空结构）→ 追加规则 → 写回；重复规则去重
**依赖**：yaml（已有）、fnmatch、pathlib

### 模块 4：`permissions/gate.py`
**职责**：层序编排与模式兜底，纯计算、不阻塞。
**对外接口**：`PermissionGate`（构造、check、grant_session、grant_permanent、cycle_mode）
**check() 层序**（F7）：
1. `run_command` → 黑名单：命中 → DENY（「命中黑名单: <描述>」）
2. 五个文件工具 → 沙箱：越界 → DENY（「路径越界: 仅限项目目录内」）
   - 参数映射：read_file/write_file/edit_file → `file_path`；find_files/grep_code → 可选 `path`（缺省 "." 免检）
3. 规则合并：命中 → 按 deny>ask>allow 裁决，reason 注明来源类型
4. 模式兜底：未命中 → default 档（只读 ALLOW / 副作用 ASK）、strict 档（只读 ALLOW / 副作用 DENY）、accept 档（ALLOW）
**依赖**：blacklist、sandbox、rules、tools.base（Tool/ToolCall）

### 模块 5：Agent Loop 接入（`agent/loop.py` + `agent/events.py`）
**职责**：把闸门接入执行流；ask 暂停/应答；拒绝回灌。
**对外接口**：`Agent(gate=None)` 构造、`answer_permission(allow)`、新事件 `PermissionAskEvent(call, reason)`
**逻辑**：
- 构造：gate 为 None 时建默认闸门（root=cwd、default 档、空规则）——既有单测构造方式不破坏
- `run()` 执行阶段改为**预检直通**：对每个将通过计划模式过滤的调用按模型原始顺序 `gate.check()`
  - ALLOW → 进执行队列（只读并发/副作用串行沿用现逻辑）
  - DENY → 立即产出失败 ToolResult（含 reason），不执行
  - ASK → 发 `PermissionAskEvent` 后挂起等待：pending future + 轮询取消标志（复用 `_CANCEL_POLL_INTERVAL` 节奏）；`answer_permission(allow)` 经 `loop.call_soon_threadsafe` 置结果（主线程/事件循环线程均可调）
- `cancel()` 同时解除 pending：置 False 并触发 `_Cancelled`，与既有取消语义一致
- 回灌顺序仍按模型原始调用顺序（拒绝结果与执行结果混排按序）
**依赖**：gate、events、executor

### 模块 6：TUI 接入（`tui/app.py` + `tui/commands.py`）
**职责**：询问交互、档位展示、/mode 命令。
**对外接口**：`run(..., gate)` 新参数；`handle_command` 增加 `/mode`
**逻辑**：
- `_consume_events` 遇到 `PermissionAskEvent`：关闭 Live 后打印询问面板（工具名+参数摘要+reason+选项 `[y] 本次 [s] 本会话 [a] 永久 [n] 拒绝`），用独立简单 prompt 等待输入
- y → 仅放行；s → `gate.grant_session(精确规则)` + 放行；a → `gate.grant_permanent(精确规则)`（写本地级 YAML）+ 放行；n → 拒绝；随后 `agent.answer_permission(bool)`
- 询问期间 Ctrl+C → `agent.cancel()`（既有取消链路）；EOF → 按拒绝处理（循环继续，模型可调整）
- `_drain_cancelled` 补处理流中可能出现的 PermissionAskEvent（取消竞态时直接跳过）
- 欢迎语加一行「权限模式: default/strict/accept」；`/mode` 循环切换三档并反馈当前档位；COMMAND_NAMES 与 /help 同步
**依赖**：gate、agent 事件

### 模块 7：配置与装配（`config.py` + `cli.py`）
**职责**：permission_mode 读取、闸门装配。
**对外接口**：`load_permission_mode(path) -> Mode`（缺失 → DEFAULT；非法 → ConfigError）
**逻辑**：cli.py 构造 `RuleSet` → `load_from` 三路径 → `PermissionGate(root=cwd, rules, mode)` → 传入 `tui.app.run`；config.yaml 顶层新增可选字段 `permission_mode`
**依赖**：yaml、permissions 包

## 模块交互

**一次带 ask 的调用全程**

```
用户输入 → TUI._agent_round → run_in_background(agent, text)   [后台线程: agent-loop]
  └─ Agent.run() 流式收模型 ToolCall 列表 → 计划模式过滤（副作用工具沿用 ch05 拦截）
     └─ 预检直通（按模型原始调用顺序，逐调用）:
          gate.check(tool, call)
            ├─ run_command ──→ blacklist.find_hit(command)    命中→DENY
            ├─ 五个文件工具 ─→ sandbox.check_inside(root, 路径) 越界→DENY
            ├─ ruleset.verdict(tool, 值, 模式兜底)             命中→allow/ask/deny
            └─ 未命中 → 模式兜底（default/strict/accept）
          ├─ ALLOW → 执行队列（只读并发、副作用串行，沿用现分批）
          ├─ DENY  → ToolResult.failure(reason) 入结果表（不执行）
          └─ ASK   → [主线程] PermissionAskEvent ──→ TUI 打印询问面板 → 用户选 y/s/a/n
                       s/a 时先 gate.grant_session / grant_permanent 登记规则
                     → agent.answer_permission(allow)
                       [后台线程] pending future 解除 → allow 入执行队列 / deny 入结果表
     └─ 结果按模型原始顺序回灌 session + ToolResultEvent → 下一轮 LLM（拒绝不终止循环）
```

事件流方向：Agent（后台线程）→ queue → TUI（主线程）；应答反向走 `answer_permission`（call_soon_threadsafe 跨线程）。

## 文件组织

```
mewcode/
├── permissions/
│   ├── __init__.py      — 导出 PermissionGate/Mode/Action/Verdict/Rule/RuleSet 等
│   ├── blacklist.py     — 内置黑名单正则表 + find_hit
│   ├── sandbox.py       — check_inside（resolve + 前缀判断）
│   ├── rules.py         — Rule/RuleSet、三 YAML 读写合并、匹配与裁决
│   └── gate.py          — PermissionGate：层序编排、模式兜底
├── agent/
│   ├── events.py        — 修改：+ PermissionAskEvent
│   └── loop.py          — 修改：gate 接入、预检直通、ask 挂起/应答、cancel 解除 pending
├── config.py            — 修改：+ load_permission_mode
├── cli.py               — 修改：装配 RuleSet/PermissionGate 传入 TUI
└── tui/
    ├── app.py           — 修改：PermissionAskEvent 交互、欢迎语档位行
    └── commands.py      — 修改：+ /mode 循环切换
tests/
├── test_blacklist.py    — 新建：9 条黑名单正反例
├── test_sandbox.py      — 新建：越界/符号链接逃逸/项目内放行
├── test_rules.py        — 新建：匹配、合并优先级、YAML 读写、损坏报错
├── test_gate.py         — 新建：层序短路、模式兜底、grant 两式
├── test_permission_loop.py — 新建：Agent Loop 内 ask 暂停/应答、五种拒绝不终止
└── test_config.py       — 修改：+ permission_mode 解析
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 闸门位置 | Agent Loop 执行前预检（`loop.py`），不动 `execute_call` 与工具本体 | 执行入口唯一、层序可测；工具代码零改动，ch04/05 行为语义不散 |
| ask 机制 | 事件 + pending future + `answer_permission`，而非同步回调 | Agent 不感知界面（沿用事件契约）；测试可直接消费事件流应答；取消经 future 解除统一处理 |
| 会话/永久允许登记的规则 | **精确值**（如 `run_command(git status)`），非前缀 `git *` | 最小授权：一次放行只覆盖被询问的那次调用形态；用户想要宽模式可在 YAML 手写 glob |
| 沙箱判定 | `resolve(strict=False)` + `is_relative_to`，root 启动时 resolve 一次 | 覆盖中间符号链接与「目标不存在」两种逃逸；Python 3.9+ 标准库自带 |
| glob 语义 | fnmatch，`**` 归一为 `*`（`*` 跨路径分隔符） | 标准库即可满足「精确 + glob」；跨分隔符匹配对命令模式（`git *`）是需要的 |
| 坏规则文件 | 启动报 ConfigError（fail-closed） | 宁可不启动，也不带着可能缺失的 deny 规则跑（spec F3） |
| 规则里未知工具名/非法 action | ConfigError | 拼写错误静默失效最危险，fail-closed 口径一致 |
| gate 默认构造 | `Agent(gate=None)` → 默认闸门（cwd 根、default 档、空规则） | 既有单测与调用方构造签名不破坏；新行为测试显式传 gate |
| 询问 UI | 独立简单 prompt（不复用主输入 PromptSession） | 避免历史/补全干扰；Ctrl+C/EOF 处理路径独立清晰 |
| 询问期 Ctrl+C / EOF | Ctrl+C → 取消本轮（既有语义）；EOF → 按拒绝 | 取消优先级最高且用户熟悉；EOF 拒绝保证循环不悬挂、模型可调整 |
| ToolCallStarted 时机 | 仍对每个调用（含将被拒的）先发 | 被拒调用也可见「调了什么→被拒原因」完整链路，TUI 展示路径零改动 |
| 黑名单覆盖 | 仅 `run_command` 的 command 参数 | 文件路径类风险由沙箱与 deny 规则覆盖，两层职责不重叠 |
| /mode 写入 | 仅会话内切换 + config.yaml `permission_mode` 决定启动档 | 档位是全局开关不是规则，不混入三个规则文件 |
