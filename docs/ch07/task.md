# 五层防御权限系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `mewcode/permissions/__init__.py` | 包导出 |
| 新建 | `mewcode/permissions/blacklist.py` | 黑名单正则表 + find_hit |
| 新建 | `mewcode/permissions/sandbox.py` | check_inside 路径硬边界 |
| 新建 | `mewcode/permissions/rules.py` | Action/Rule/RuleSet、匹配裁决、三 YAML 读写 |
| 新建 | `mewcode/permissions/gate.py` | Mode/Verdict/PermissionGate 层序编排 |
| 修改 | `mewcode/agent/events.py` | + PermissionAskEvent |
| 修改 | `mewcode/agent/loop.py` | gate 接入、预检直通、ask 挂起/应答、cancel 解除 |
| 修改 | `mewcode/config.py` | + load_permission_mode |
| 修改 | `mewcode/cli.py` | 装配 RuleSet/PermissionGate 传入 TUI |
| 修改 | `mewcode/tui/app.py` | 询问交互、欢迎语档位行 |
| 修改 | `mewcode/tui/commands.py` | + /mode |
| 新建 | `tests/test_blacklist.py` `tests/test_sandbox.py` `tests/test_rules.py` `tests/test_gate.py` `tests/test_permission_loop.py` | 五个新测试文件 |
| 修改 | `tests/test_config.py` `tests/test_agent_loop.py` | permission_mode 解析；副作用工具测试适配 |
| 修改 | `scripts/mock_llm.py` | + danger-model（黑名单/沙箱 e2e 用） |
| 修改 | `docs/ch05/checklist.md` | 场景 5 与集成条目随 N4 行为变更更新 |

## T1: RuleSet 核心（类型/匹配/裁决）

**文件：** `permissions/rules.py`、`tests/test_rules.py`
**依赖：** 无
**步骤：**
1. 定义 `Action` 枚举（allow/ask/deny）与 `Rule` dataclass（tool/pattern/action）
2. `RuleSet`：`add`、`match(tool, value)`（先精确相等再 fnmatch，`**` 归一 `*`）、`verdict(tool, value, default)`（deny>ask>allow，无命中 → default）
3. 写匹配与裁决单测（精确/glob/优先级/无命中默认值）

**验证：** `python -m pytest tests/test_rules.py -q` 全绿

## T2: 三 YAML 读写合并

**文件：** `permissions/rules.py`、`tests/test_rules.py`
**依赖：** T1
**步骤：**
1. `load_from(paths, local_path)`：逐文件读 `rules` 列表，缺失跳过；损坏 YAML/结构非法/未知工具名/非法 action → ConfigError（含文件名）
2. `append_to_local_file(rule)`：读本地级（缺失新建）→ 去重追加 → 写回
3. 单测：合并、缺文件跳过、损坏报错、非法条目报错、追加去重、写回后再读一致

**验证：** `python -m pytest tests/test_rules.py -q` 全绿

## T3: 黑名单

**文件：** `permissions/blacklist.py`、`tests/test_blacklist.py`
**依赖：** 无
**步骤：**
1. `BLACKLIST`：9 条 (re.Pattern, 中文描述)（fork bomb、mkfs、dd、sudo、rm -rf / 与 /*、curl|sh、shutdown 族、> /dev/ 覆写、chmod -R /）
2. `find_hit(command)` 返回命中描述
3. 单测：每条正例命中、反例（`rm -rf build/`、`git status`、`pip install`）不误伤

**验证：** `python -m pytest tests/test_blacklist.py -q` 全绿

## T4: 路径沙箱

**文件：** `permissions/sandbox.py`、`tests/test_sandbox.py`
**依赖：** 无
**步骤：**
1. `check_inside(root, path)`：相对路径拼 root → `resolve(strict=False)` → `is_relative_to(root.resolve())`
2. 单测：项目内放行、`../` 越界、绝对路径越界、指向项目外符号链接拒绝、指向项目内符号链接放行、不存在目标（父路径判定）

**验证：** `python -m pytest tests/test_sandbox.py -q` 全绿

## T5: 闸门层序编排

**文件：** `permissions/gate.py`、`permissions/__init__.py`、`tests/test_gate.py`
**依赖：** T1–T4
**步骤：**
1. `Mode` 枚举 + 中文标签映射、`Verdict` dataclass
2. `PermissionGate(root, rules, mode)`：`check(tool, call)` 按层序（run_command→黑名单；五文件工具→沙箱，参数映射 file_path/path；规则 verdict；模式兜底）
3. `grant_session`/`grant_permanent`/`cycle_mode`
4. 单测：各层短路顺序、三档兜底行为、accept 档下 deny 规则仍拦、cycle 循环

**验证：** `python -m pytest tests/test_gate.py -q` 全绿

## T6: permission_mode 配置解析

**文件：** `mewcode/config.py`、`tests/test_config.py`
**依赖：** T5
**步骤：**
1. `load_permission_mode(path)`：顶层可选 `permission_mode`，缺失 → DEFAULT，值非法 → ConfigError
2. 单测：缺失/三档合法/非法值报错

**验证：** `python -m pytest tests/test_config.py -q` 全绿

## T7: PermissionAskEvent 事件

**文件：** `mewcode/agent/events.py`
**依赖：** 无
**步骤：**
1. 新增 `PermissionAskEvent(call, reason)` dataclass
2. 并入 `AgentEvent` 联合类型

**验证：** `python -m compileall mewcode/` 通过

## T8: Agent 接入闸门（预检直通，无 ask 挂起）

**文件：** `mewcode/agent/loop.py`
**依赖：** T5、T7
**步骤：**
1. 构造加 `gate: PermissionGate | None = None`（None → 默认闸门）
2. `permission_mode` 属性与 `cycle_permission_mode()` 委托 gate
3. `run()` 执行阶段改为逐调用预检：计划模式过滤沿用 → `gate.check` → ALLOW 进执行队列（只读并发/副作用串行沿用）、DENY 立即失败结果不执行；回灌与事件仍按原始调用顺序

**验证：** `python -m pytest tests/test_plan_mode.py -q` 全绿（test_agent_loop 副作用用例 T13 适配后复验）

## T9: ask 挂起/应答与取消解除

**文件：** `mewcode/agent/loop.py`、`tests/test_permission_loop.py`
**依赖：** T8
**步骤：**
1. `_wait_permission()`：pending future + `_CANCEL_POLL_INTERVAL` 轮询取消
2. `answer_permission(allow)`：`call_soon_threadsafe` 置结果
3. `cancel()` 解除 pending
4. run() 中 ASK → 发 `PermissionAskEvent` → 等待 → allow 入队列 / deny 失败结果
5. 单测：ask 事件序列、应答后执行、拒绝后循环继续（拒绝不终止）、取消解除 pending、黑名单/沙箱/deny 规则/严格档/用户拒绝五种拒绝路径均 RoundEnd(OK) 收尾

**验证：** `python -m pytest tests/test_permission_loop.py -q` 全绿

## T10: CLI 装配

**文件：** `mewcode/cli.py`
**依赖：** T2、T6
**步骤：**
1. 构造 RuleSet → `load_from` 三路径（用户级 `~/.mewcode/permissions.yaml`、项目级/本地级 `<cwd>/.mewcode/`）
2. `PermissionGate(root=cwd, rules, mode=load_permission_mode(args.config))`
3. 传入 `tui.app.run`

**验证：** `python -m compileall mewcode/` 通过；`python -m mewcode --help` 正常

## T11: /mode 命令

**文件：** `mewcode/tui/commands.py`
**依赖：** T8
**步骤：**
1. COMMAND_NAMES 加 `/mode`，/help 补说明
2. `handle_command`：`agent.cycle_permission_mode()`，反馈含新档位中文名

**验证：** `python -m compileall mewcode/` 通过

## T12: TUI 询问交互

**文件：** `mewcode/tui/app.py`
**依赖：** T9、T11
**步骤：**
1. `run()` 加 `gate` 参数，欢迎语加「权限模式: <档位>」行
2. `_consume_events` 处理 `PermissionAskEvent`：关 Live → 打印询问面板（工具名+参数摘要+reason+`[y] 本次 [s] 本会话 [a] 永久 [n] 拒绝`）→ 独立 prompt 循环直到合法输入 → y 放行 / s `grant_session(精确规则)` / a `grant_permanent(精确规则)` / n 拒绝 → `agent.answer_permission`
3. Ctrl+C → `agent.cancel()`（沿既有取消路径）；EOF → 按拒绝
4. `_drain_cancelled` 跳过残留 `PermissionAskEvent`

**验证：** `python -m compileall mewcode/` 通过；tmux 手测交互（T16）

## T13: 既有测试适配

**文件：** `tests/test_agent_loop.py`
**依赖：** T8
**步骤：**
1. 执行副作用工具的测试构造 Agent 时显式传 `PermissionGate(root=cwd, rules=RuleSet(), mode=Mode.ACCEPT)`（等价 ch05 直接执行语义）
2. 断言点不变

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_plan_mode.py -q` 全绿

## T14: ch05 checklist 行为变更同步

**文件：** `docs/ch05/checklist.md`
**依赖：** 无
**步骤：**
1. 场景 5 与集成条目（命令不弹确认直接执行）改为「默认档下副作用工具转为询问，放行档下直接执行（ch07 N4）」
2. 注明变更原因

**验证：** 通读确认与 ch07 spec N4 一致

## T15: 全量回归

**依赖：** T1–T14
**步骤：**
1. `python -m pytest -q` 全绿
2. `python -m compileall mewcode/ scripts/` 无输出错误

**验证：** 命令输出全绿

## T16: mock 新增 danger-model

**文件：** `scripts/mock_llm.py`
**依赖：** 无
**步骤：**
1. 新增 danger-model（Anthropic 与 OpenAI 两条协议路径）：单轮三个调用——`run_command("sudo rm -rf /")`、`write_file(file_path="../escape.txt")`、`write_file(file_path="note_ok.txt")`
2. 分派表（has_tools and model == "danger-model"）两处接入

**验证：** `python -m compileall scripts/` 通过；curl mock 服务返回三个 tool_use 块

## T17: tmux 端到端验收

**依赖：** T15、T16
**步骤：**
1. tmux 启动 mock LLM + mewcode
2. 按 ch07 checklist 场景逐项操作观察

**验证：** ch07 checklist 全项通过

## 执行顺序

```
T1 → T2 → T5 → T8 → T9 → T12 ──→ T15 → T17
T3 ──┘   T6 ─┘   T7 ─┘   T11 ──┘   T13 ─┘   T16 ─┘
T4 ──┘       T10 ──────────┘   T14 ─┘
```

（T3/T4 与 T1/T2 可并行；T14、T16 独立）
