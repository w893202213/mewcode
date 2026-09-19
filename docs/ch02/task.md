# Ctrl+C 双击退出 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/tui/double_press.py` | `DOUBLE_PRESS_WINDOW`、`ExitSignal`、`DoublePressTracker` |
| 新建 | `tests/test_double_press.py` | `DoublePressTracker` 单元测试（假时钟） |
| 修改 | `mewcode/tui/app.py` | `c-c` 绑定、bottom_toolbar、哨兵识别、流式中断回滚 |

## T1: 实现 DoublePressTracker

**文件：** `mewcode/tui/double_press.py`（新建）
**依赖：** 无
**步骤：**
1. 定义模块常量 `DOUBLE_PRESS_WINDOW = 3.0`
2. 定义 `ExitSignal(Enum)`，成员 `EXIT = auto()`
3. 定义 `DoublePressTracker`：
   - `__init__(window: float = DOUBLE_PRESS_WINDOW, clock: Callable[[], float] = time.monotonic)`：字段 `_window`、`_clock`、`_first_press_at: float | None`
   - `press() -> bool`：`_first_press_at` 为 None 或已过期 → 记录当前时刻返回 False；窗口内 → 清空状态返回 True
   - `reset() -> None`：`_first_press_at = None`
   - `hint_active() -> bool`：非 None 且 `clock() - _first_press_at < _window`
   - 边界语义：`elapsed >= window` 算过期（恰好 3.0 秒过期）

**验证：** `.venv/bin/python -m compileall mewcode/tui/double_press.py` 通过

## T2: DoublePressTracker 单元测试

**文件：** `tests/test_double_press.py`（新建）
**依赖：** T1
**步骤：**
1. 定义 `FakeClock` 类：持有可手动推进的 `now` 属性，`__call__()` 返回 `now`
2. 用例：
   - 首次 `press()` 返回 False，`hint_active()` 为 True
   - 窗口内（推进 2.9s）第二次 `press()` 返回 True，且状态清空（`hint_active()` False）
   - 过期（推进 3.0s）后 `press()` 返回 False，重新开始新窗口
   - `reset()` 后 `hint_active()` False，`press()` 从首次算起
   - 恰好 3.0 秒算过期（`hint_active()` False）

**验证：** `.venv/bin/python -m pytest tests/test_double_press.py -v` 全绿

## T3: 输入行双击退出集成

**文件：** `mewcode/tui/app.py`（修改 `run()`）
**依赖：** T1
**步骤：**
1. 顶部 import `ExitSignal`、`DoublePressTracker` 与 prompt_toolkit 的 `KeyBindings`
2. `run()` 内创建 `tracker = DoublePressTracker()`；构造 KeyBindings 注册 `c-c`：
   ```python
   if tracker.press():
       event.app.exit(result=ExitSignal.EXIT)
   else:
       event.app.invalidate()  # 立即重绘，让提示出现
   ```
3. `PromptSession` 增加参数：`key_bindings=kb`、`bottom_toolbar=<回调：hint_active 时返回 "再按一次 Ctrl+C 退出"，否则 None>`、`refresh_interval=0.5`
4. 主循环 `prompt()` 返回后先判哨兵：`if text is ExitSignal.EXIT: return`（正常返回 → 退出码 0）
5. 用 3 行小脚本先验证 bottom_toolbar 回调返回 None 是否隐藏工具栏；若不隐藏，回调退回返回空字符串并在代码注释说明

**验证：** `compileall` 通过 + tmux 冒烟（mock 服务 8989 + mewcode 启动）：输入若干字符后按一次 C-c → 字符仍在、提示出现；再按一次 C-c（间隔 <3s）→ 进程退出、回到 shell、退出码 0

## T4: 流式中断处理

**文件：** `mewcode/tui/app.py`（修改 `_chat_round`）
**依赖：** T1、T3
**步骤：**
1. `_chat_round` 签名增加 `tracker: DoublePressTracker` 参数，`run()` 调用处传入
2. 在 `except ProviderError` 之后新增 `except KeyboardInterrupt` 分支：
   - `del session.messages[start_index:]`（F4：未完成轮次不入上下文）
   - 打印 `Panel("已中断，本轮回复未保存。", title="[bold yellow]回复中断[/bold yellow]", border_style="yellow")`
   - `tracker.reset()`（F3：计数从头开始）

**验证：** `compileall` 通过 + tmux 冒烟：mock 流式进行中按一次 C-c → 回复停止、回到输入行、无 traceback；随后按一次 C-c → 只提示不退出（计数已重置）；`/exit` 正常退出

## T5: 全量回归

**文件：** 无（运行测试）
**依赖：** T2、T3、T4
**步骤：**
1. `.venv/bin/python -m pytest tests/ -v`（含新单测与既有 test_config/test_providers/test_session）
2. `.venv/bin/python -m compileall mewcode/`

**验证：** 输出全部 passed，无警告异常

## 执行顺序

```
T1 → T2 → T3 → T4 → T5
```
