# 输入回显移除与思考期计时提示 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/tui/app.py` | ThinkingIndicator、_stream_renderable、_chat_round 改造 |
| 新建 | `tests/test_thinking_indicator.py` | 计时渲染、三态组装、无回显的单元测试 |
| 修改 | `scripts/mock_llm.py` | MOCK_DELAY 环境变量（默认 0.05 不变） |

## T1: ThinkingIndicator 与渲染组装函数

**文件：** `mewcode/tui/app.py`
**依赖：** 无
**步骤：**
1. 补充导入：`import time`、`from collections.abc import Callable`。
2. 定义 `ThinkingIndicator` 类：构造参数 `clock: Callable[[], float] = time.monotonic`，构造时记录 `self._start = clock()`；`__rich_console__` 计算 `int(self._clock() - self._start)` 并 `yield Text(f"thinking… {n}s", style="grey50")`。
3. 定义 `_stream_renderable(thinking: str, reply: str, indicator: ThinkingIndicator) -> Group`：reply 非空 → `Group(Markdown(reply))`；thinking 非空 → `Group(indicator, Text(thinking, style="dim"))`；否则 → `Group(indicator)`。
4. 删除旧 `_thinking_renderable` 函数与 `_THINKING_HEADER` 常量。

**验证：** `.venv/bin/python -m compileall mewcode/` 无语法错误。

## T2: _chat_round 接入

**文件：** `mewcode/tui/app.py`
**依赖：** T1
**步骤：**
1. 删除 `console.print(f"{_USER_PREFIX} {text}")` 一行与 `_USER_PREFIX` 常量定义。
2. 在 `console.print(_ASSISTANT_PREFIX)` 之后、`with Live(...)` 之前创建 `indicator = ThinkingIndicator()`（计时起点 ≈ 请求发出时刻）。
3. `Live` 初始 renderable 由 `Markdown("")` 改为 `indicator`。
4. 事件循环中 `ThinkingDelta` 与 `TextDelta` 两个分支的 `live.update(_thinking_renderable(...))` 统一改为 `live.update(_stream_renderable(thinking_text, "".join(reply_buffer), indicator))`。
5. 保持 `Done` 分支、错误/中断分支原样不动。

**验证：** `.venv/bin/python -m compileall mewcode/` 通过；`.venv/bin/python -m pytest tests/ -q` 既有测试全部通过。

## T3: 单元测试

**文件：** `tests/test_thinking_indicator.py`（新建）
**依赖：** T2
**步骤：**
1. 定义 `FakeClock`（仿 `tests/test_double_press.py`：`now` 属性、`advance(seconds)`、可调用）。
2. `test_indicator_seconds_tick`：`ThinkingIndicator(clock)`，用 `Console(record=True, force_terminal=False)` 渲染，`export_text()` 断言含 `thinking… 0s`；`clock.advance(3)` 后再渲染断言含 `thinking… 3s`；`export_text(styles=True)` 或检查 span style 含 `grey50`。
3. `test_stream_renderable_states`：用假 clock 构造 indicator，分别断言三种状态：
   - reply 空、thinking 空 → 渲染文本含 `thinking…`；
   - reply 空、thinking 非空 → 渲染文本同时含 `thinking…` 与思考内容；
   - reply 非空 → 渲染文本不含 `thinking…`、含回复正文。
4. `test_chat_round_no_user_echo`：写一个极简假 Provider（`stream` 依次产出 `TextDelta("你好")` 和 `Done()`），`Console(record=True, force_terminal=False)`，`ChatSession()`，调用 `_chat_round(console, provider, session, "我的问题", DoublePressTracker())`；`export_text()` 断言不含 `▸ 你`、不含 `我的问题`，含 `你好`。

**验证：** `.venv/bin/python -m pytest tests/test_thinking_indicator.py -q` 全部通过；`.venv/bin/python -m pytest tests/ -q` 全量通过。

## T4: mock 支持 MOCK_DELAY 环境变量

**文件：** `scripts/mock_llm.py`
**依赖：** 无（可与 T1-T3 并行）
**步骤：**
1. 顶部补充 `import os`。
2. `MockHandler` 增加类属性 `delay: float = 0.05`（默认值保持不变）。
3. `_send_sse(self, events: list[str], delay: float | None = None)`：`delay` 为 None 时使用 `self.delay`；`do_POST` 中三处调用去掉写死的 `delay=0.05` 参数。
4. `main()` 中：`MockHandler.delay = float(os.environ.get("MOCK_DELAY", "0.05"))`。

**验证：**
- 默认启动 `python scripts/mock_llm.py`，curl POST `/v1/messages`，流返回仍为 0.05s 间隔（行为不变）；
- `MOCK_DELAY=0.5 python scripts/mock_llm.py`，同上请求，流返回间隔明显变慢。

## T5: tmux 端到端验收

**文件：** 无代码改动，产出验收报告
**依赖：** T3、T4
**步骤：**
1. 终端 A：`MOCK_DELAY=2 python scripts/mock_llm.py 8989` 启动 mock。
2. tmux 会话中启动 MewCode（配置指向 mock，thinking 开启）。
3. 输入问题回车，逐项观察并记录：
   - 屏幕上问题只出现一次（无 `▸ 你` 回显）；
   - 回车后立即出现灰色 `thinking…`，秒数每 1 秒跳动；
   - 思考增量流式期间计时仍显示，思考文本灰色；
   - 首个正文 token 到达后计时消失，正文正常流式。
4. 用 thinking 关闭的配置重复第 3 步（静默期 → 正文，同样有计时）。
5. 按 `docs/ch03/checklist.md` 逐项打勾，输出验收报告。

**验证：** checklist 全部条目通过；记录实际观察证据（截取终端输出或描述行为）。

## 执行顺序

```
T1 → T2 → T3
T4（可并行）───→ T5
```
