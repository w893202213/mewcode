# 输入回显移除与思考期计时提示 Plan

## 架构概览

本次改动全部集中在 TUI 渲染层（`mewcode/tui/app.py` 一个文件），不触碰 provider、session、config 等任何其他模块：

- **ThinkingIndicator**：时间感知的 Rich renderable。构造时记录起点（单调时钟），每次被渲染时实时计算经过秒数，输出灰色 `thinking… Ns` 文本。它不需要任何线程：rich `Live` 的 auto_refresh 线程会周期性重渲染当前 renderable（已核实 rich 15 源码：`Live.refresh()` → `set_renderable()`），秒数随之自动跳动，主线程可以继续阻塞在 `provider.stream()` 迭代上。
- **渲染组装函数**：把「思考状态」的三种形态统一成一个函数，替代现有的 `_thinking_renderable`。回复正文开始后，计时与思考区一并隐去。
- **`_chat_round` 改造**：删除用户输入回显；在发出请求前创建 indicator 并作为 Live 的初始 renderable。

## 核心数据结构

### ThinkingIndicator（类，定义于 `mewcode/tui/app.py`）

| 成员 | 说明 |
|------|------|
| `__init__(self, clock: Callable[[], float] = time.monotonic)` | 构造时记录起点 `self._start = clock()`；clock 可注入，便于单元测试（与 `DoublePressTracker(clock=...)` 风格一致） |
| `__rich_console__(self, console, options)` | 渲染时计算 `int(clock() - _start)`，产出灰色 `Text(f"thinking… {n}s")`（样式 `grey50`） |

### `_stream_renderable(thinking: str, reply: str, indicator: ThinkingIndicator) -> Group`

组装 Live 渲染内容，按状态三选一：

| 状态 | 条件 | 渲染内容 |
|------|------|---------|
| 思考期（静默） | `reply` 为空、`thinking` 为空 | 仅灰色计时行 `thinking… Ns` |
| 思考期（有思考内容） | `reply` 为空、`thinking` 非空 | 计时行在上 + 灰色思考文本在下 |
| 回复中 | `reply` 非空 | 仅 Markdown 回复（计时与思考区隐去，满足 F4 的停止时机） |

## 模块设计

### `mewcode/tui/app.py`（修改）

**职责：** TUI 主循环与一轮对话的流式渲染（不变），新增计时提示渲染（新增）。

**改动点：**
1. 删除 `console.print(f"{_USER_PREFIX} {text}")` 与 `_USER_PREFIX` 常量（F1）。prompt_toolkit 输入行本身留在终端滚动区，用户问题天然可见且只出现一次。
2. 在 `console.print(_ASSISTANT_PREFIX)` 之后、进入 `Live` 之前创建 `indicator = ThinkingIndicator()`；`Live(indicator, ...)` 初始渲染即为灰色计时行（F2）——从请求发出的瞬间起，静默期就有提示。
3. 事件循环中 `ThinkingDelta` / `TextDelta` 分支统一调用 `live.update(_stream_renderable(thinking_text, "".join(reply_buffer), indicator))`（F3/F4）。
4. 删除旧 `_thinking_renderable` 与 `_THINKING_HEADER` 常量（计时行取代原「💭 思考中…」标题，信息更丰富）。
5. 错误（`ProviderError`）与中断（`KeyboardInterrupt`）分支不动（F5）。

**依赖：** 现有依赖不变（rich、time）；无新依赖。

### `scripts/mock_llm.py`（修改，测试辅助）

**职责：** 支持 `MOCK_DELAY` 环境变量（默认 `0.05` 不变），控制流事件发送间隔。

**说明：** 这是对已批准 spec「不做的事：不改动 mock 服务」的**显式补充**——不改协议、不新增事件，只让静默期在 e2e 中可观察（如 `MOCK_DELAY=2` 时首个事件前有 2 秒静默，计时提示可被肉眼确认）。请随本 plan 一并审批。

## 模块交互

```
run()
 └─ _chat_round(console, provider, session, text, tracker)
      ├─ 删除用户消息回显（F1）
      ├─ console.print(_ASSISTANT_PREFIX)          # 助手前缀行
      ├─ indicator = ThinkingIndicator()           # 计时起点 ≈ 请求发出时刻
      ├─ with Live(indicator, console=...):        # 初始即显示灰色 thinking… 0s
      │     ├─ [auto_refresh 线程] 每 0.1s refresh() → 重渲染 indicator → 秒数跳动
      │     └─ for event in provider.stream(...):
      │           ThinkingDelta → thinking_text +=  → live.update(_stream_renderable(...))
      │           TextDelta     → reply_buffer +=   → live.update(...)  # reply 非空→计时隐去
      │           Done          → 循环结束
      └─ session.add_assistant(...)                # 落盘（不变）
```

## 文件组织

```
mewcode/tui/app.py                 — ThinkingIndicator、_stream_renderable、_chat_round 改造
tests/test_thinking_indicator.py   — 新增：计时渲染、状态组装、无回显的单元测试
scripts/mock_llm.py                — MOCK_DELAY 环境变量（测试辅助）
docs/ch03/spec.md                  — 已批准
docs/ch03/plan.md                  — 本文档
docs/ch03/task.md / checklist.md   — 后续产出
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 计时刷新机制 | 时间感知 renderable + rich Live 的 auto_refresh 周期重渲染 | 零新线程、零异步改造（N3）；已核实 rich 15 `Live.refresh()` 会重渲染当前 renderable |
| 时钟 | `time.monotonic()`，构造参数可注入 | 单调时钟不受系统时间调整影响（N2）；注入便于单测，与 `DoublePressTracker` 风格一致 |
| 计时粒度 | 整秒（`int(...)`） | spec F3「经过的整秒数」 |
| 计时行样式 | `grey50` | 用户明确要求灰色；思考文本保持现有 `dim` |
| 计时文案 | `thinking… Ns` | 采用用户原词 |
| `refresh_per_second` | 保持 10 不变 | 流式增量上屏延迟是既有行为，10Hz 满足 ≥1Hz 的计时要求，不扩大改动面 |
| 计时终点判定 | `reply` 非空即隐去计时 | 首个正文增量使 reply 非空，天然对应「首个回复 token 到达」（已批准的选择） |
| 思考块标题 | 删除「💭 思考中…」标题，由计时行兼任 | 计时行（thinking… Ns）+ 灰色思考文本，信息量严格增加，避免两行标题叠床架屋 |
| 用户输入回显 | 完全删除，不保留任何 `▸ 你` 前缀形式 | spec F1：原样输出无信息增量；会话记录由 `session.add_user` 承担，不受影响 |
| e2e 可观察性 | mock 增加 `MOCK_DELAY` 环境变量（默认 0.05 不变） | 不改协议、不改生产代码；仅让静默期/思考期在 e2e 中肉眼可验证（对 spec 的显式补充，请审批） |
