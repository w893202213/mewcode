# Ctrl+C 双击退出 Plan

## 架构概览

改动集中在 TUI 层，分两块：

1. **输入行阶段的双击判定**（F1/F2）：用 prompt_toolkit 自定义 `c-c` key binding 覆盖默认的「Ctrl+C → KeyboardInterrupt」。第一次按下不离开 prompt（已输入 buffer 天然保留），通过 bottom_toolbar 显示提示；窗口内第二次按下用 `app.exit(result=ExitSignal.EXIT)` 让 `prompt()` 返回哨兵值，主循环识别后正常 `return`（退出码 0）。
2. **流式阶段的中断**（F3/F4）：`_chat_round` 内新增 `except KeyboardInterrupt` 分支，复用现有请求失败的回滚逻辑，打印「已中断」面板，重置双击计数后回到输入行。

双击判定抽成纯逻辑类 `DoublePressTracker`（时钟可注入），放在独立模块便于单元测试。

## 核心数据结构

### DoublePressTracker（新建 `mewcode/tui/double_press.py`）

```python
DOUBLE_PRESS_WINDOW = 3.0  # 双击窗口（秒），固定值，不做配置

class ExitSignal(Enum):
    """prompt() 的哨兵返回值：区别于普通文本输入。"""
    EXIT = auto()

class DoublePressTracker:
    """双击 Ctrl+C 判定：第一次按下开始计时，窗口内第二次按下判为退出。

    时钟可注入（默认 time.monotonic），满足 N2 且便于单测。
    """

    def __init__(
        self,
        window: float = DOUBLE_PRESS_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None: ...
        # 字段：_window、_clock、_first_press_at: float | None（None 表示未开始计数）

    def press(self) -> bool:
        """记录一次 Ctrl+C。窗口内第二次按下返回 True（并清空状态）；
        首次按下（或已过期后的按下）返回 False。"""

    def reset(self) -> None:
        """清空计数状态。流式中断后调用，保证计数从头开始（F3）。"""

    def hint_active(self) -> bool:
        """是否处于提示窗口内（供 bottom_toolbar 渲染判断）。"""
```

**窗口边界语义：** 距首次按下不足 3.0 秒算窗口内；恰好 3.0 秒算过期。

## 模块设计

### mewcode/tui/double_press.py（新建）

**职责：** 双击判定纯逻辑，无 TUI 依赖。
**对外接口：** `DOUBLE_PRESS_WINDOW`、`ExitSignal`、`DoublePressTracker`。
**依赖：** 仅标准库（enum、time、typing）。

### mewcode/tui/app.py（修改）

**职责：** 主循环、输入处理、流式渲染（现有）；新增 Ctrl+C 绑定与中断处理。

**改动点：**

1. `run()`：
   - 创建 `tracker = DoublePressTracker()`
   - 构造 KeyBindings：注册 `c-c`，处理器逻辑：
     ```python
     if tracker.press():
         event.app.exit(result=ExitSignal.EXIT)
     else:
         event.app.invalidate()  # 立即重绘，让提示出现
     ```
   - `PromptSession` 增加参数：
     - `key_bindings=kb`（用户绑定优先于默认绑定，覆盖默认 Ctrl+C abort）
     - `bottom_toolbar=lambda: "再按一次 Ctrl+C 退出" if tracker.hint_active() else None`（返回 None 隐藏工具栏，实现时验证；若不支持则退回空字符串）
     - `refresh_interval=0.5`（提示 3 秒后自动过期消失，无需定时任务；空闲重绘开销可忽略）
   - 主循环拿到 `prompt()` 返回值后先判哨兵：`if text is ExitSignal.EXIT: return`
   - 现有 `except KeyboardInterrupt: continue` 分支保留（作为非按键来源 SIGINT 的安全网）
   - `_chat_round` 调用改为传入 `tracker`

2. `_chat_round()`：
   - 签名增加 `tracker: DoublePressTracker` 参数
   - 新增 `except KeyboardInterrupt` 分支（放在 `except ProviderError` 之后）：
     - 回滚 `del session.messages[start_index:]`（与 ProviderError 分支一致，F4）
     - 打印 `Panel("已中断", title="[bold yellow]回复中断[/bold yellow]", ...)` 提示
     - `tracker.reset()`
   - 流式期间无 prompt_toolkit Application 运行，SIGINT 走默认 Python 处理器抛 KeyboardInterrupt，`with Live(...)` 上下文正常退出

## 模块交互

```
run()
 ├─ tracker = DoublePressTracker()
 ├─ PromptSession(key_bindings=kb, bottom_toolbar=…, refresh_interval=0.5)
 └─ 主循环
      text = prompt()
      ├─ text is ExitSignal.EXIT → return          （双击退出，码 0）
      ├─ KeyboardInterrupt → continue               （安全网，保留）
      ├─ EOFError → 打印再见 → return               （Ctrl+D，不变）
      ├─ /exit → return                             （不变）
      └─ 其他 → _chat_round(console, provider, session, text, tracker)
           ├─ KeyboardInterrupt → 回滚 + 「回复中断」面板 + tracker.reset()
           └─ 正常完成 → 会话落盘（不变）
```

**输入行 Ctrl+C 流程：** 按键 `c-c` → key handler → `tracker.press()`：
- 首次 → `app.invalidate()` → toolbar 显示「再按一次 Ctrl+C 退出」；3 秒内无第二次按下则 `hint_active()` 变 False，refresh 自动清除
- 窗口内第二次 → `app.exit(result=ExitSignal.EXIT)` → `prompt()` 返回哨兵 → `run()` return

**流式中断流程：** SIGINT → KeyboardInterrupt → `_chat_round` 回滚 → 面板提示 → 回到主循环。计数天然从头开始（tracker 仅在 prompt 内被触发，且中断时已 reset）。

## 文件组织

```
mewcode/
├── tui/
│   ├── app.py          — 修改：c-c 绑定、bottom_toolbar、哨兵识别、流式中断回滚
│   └── double_press.py — 新建：DOUBLE_PRESS_WINDOW、ExitSignal、DoublePressTracker
tests/
├── test_double_press.py — 新建：DoublePressTracker 单元测试（假时钟）
docs/ch02/
├── spec.md   — 已批准
└── plan.md   — 本文档
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 拦截 Ctrl+C 的机制 | PromptSession 自定义 `c-c` key binding，覆盖默认 abort | 不离开 prompt 才能保留已输入 buffer（F1）；prompt_toolkit 用户绑定优先于默认绑定 |
| 双击状态管理 | 独立 `DoublePressTracker` 类，时钟注入 | 纯逻辑可用假时钟单测；`time.monotonic` 满足 N2 |
| 提示显示 | `bottom_toolbar` 返回提示文本 / None | 提示渲染在输入行正下方，不影响 buffer；None 隐藏工具栏 |
| 提示过期重绘 | `refresh_interval=0.5` | 无需 asyncio 定时任务，toolbar 每次重绘按时钟判断；空闲时 2 次/秒重绘开销可忽略 |
| 退出信号传递 | `app.exit(result=ExitSignal.EXIT)` 哨兵 | `prompt()` 返回 Any，哨兵 `is` 比较与文本输入区分；`run()` 识别后正常 return → 退出码 0 |
| 流式中断处理 | `_chat_round` 内捕获 KeyboardInterrupt，复用回滚 | 与 ProviderError 分支行为一致（F4），改动最小 |
| 窗口常量 | 模块常量 `DOUBLE_PRESS_WINDOW = 3.0` | spec「不做的事」明确固定 3 秒，不做配置 |
| 窗口边界 | elapsed < 3.0 算窗口内，== 3.0 算过期 | 明确边界语义，测试可断言 |

## spec 覆盖检查

- F1（一次 Ctrl+C 只提示不清行）→ 自定义 key binding 不离开 prompt + toolbar ✓
- F2（3 秒窗口双击退出）→ tracker 判定 + ExitSignal 哨兵 + 正常 return ✓
- F3（流式中断回输入行）→ `_chat_round` 捕获 KeyboardInterrupt + `tracker.reset()` ✓
- F4（中断不入上下文）→ 复用 `del session.messages[start_index:]` 回滚 ✓
- F5（/exit、Ctrl+D 不变）→ 不改动这两个分支 ✓
- N1（无新依赖）→ 全部基于 prompt_toolkit 现有机制 ✓
- N2（时间调整不影响判定）→ `time.monotonic` ✓
- N3（中文提示）→ 「再按一次 Ctrl+C 退出」✓
