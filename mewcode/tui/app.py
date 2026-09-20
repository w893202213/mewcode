"""TUI 主循环：输入、Agent 事件消费渲染、错误显示。

组合方式：prompt_toolkit PromptSession 管输入（历史/补全），Rich 管输出
（Markdown 渲染、Live 流式刷新、思考块灰色展示）。Agent 在后台线程跑
事件循环（run_in_background），事件经线程安全队列交给主线程渲染；
patch_stdout 让 Rich 与输入行共存，输入行始终停在最底部。
"""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from mewcode import __version__
from mewcode.agent import Agent, run_in_background
from mewcode.agent.events import (
    ProgressEvent,
    RoundEnd,
    RoundEndReason,
    ThinkingDelta,
    TextDelta,
    ToolCallStarted,
    ToolResultEvent,
    UsageEvent,
)
from mewcode.config import ProviderConfig
from mewcode.provider.base import Provider
from mewcode.session import ChatSession
from mewcode.tools import ToolRegistry
from mewcode.tui.commands import CommandResult, COMMAND_NAMES, handle_command
from mewcode.tui.double_press import DoublePressTracker, ExitSignal

# 渲染样式常量
_ASSISTANT_PREFIX = "[bold magenta]🐱 MewCode[/bold magenta]"
# 计时行样式（用户要求灰色，区别于正文）
_TIMER_STYLE = "grey50"
# Ctrl+C 双击退出提示（显示在输入行下方 bottom_toolbar）
_EXIT_HINT = "再按一次 Ctrl+C 退出"
# 工具调用展示截断：参数摘要 200 字符、结果面板 2000 字符（显示截断，回灌内容不受影响）
_ARGS_SUMMARY_CHARS = 200
_DISPLAY_RESULT_CHARS = 2000
# 取消后等待 RoundEnd 的最大秒数（防御：后台异常时界面不悬挂）
_CANCEL_DRAIN_TIMEOUT = 5.0


def _print_welcome(console: Console, config: ProviderConfig) -> None:
    """打印欢迎语（含配置名与模型，绝不回显 api_key）。"""
    lines = [
        f"配置: [bold]{config.name}[/bold]（协议 {config.protocol}）",
        f"模型: {config.model}",
        f"思考模式: {'开启' if config.thinking else '关闭'}",
        "输入 /help 查看可用命令。",
    ]
    console.print(
        Panel("\n".join(lines), title=f"[bold]MewCode v{__version__}[/bold]", border_style="cyan")
    )


def run(provider: Provider, config: ProviderConfig, registry: ToolRegistry) -> None:
    """TUI 主循环：读输入 → 命令或 Agent Loop → 回到输入。"""
    session = ChatSession()
    agent = Agent(provider, registry, session)

    # 双击 Ctrl+C 判定：第一次提示（不清行），3 秒内第二次退出
    tracker = DoublePressTracker()
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        """Ctrl+C：覆盖默认 abort（KeyboardInterrupt），改为双击退出。"""
        if tracker.press():
            event.app.exit(result=ExitSignal.EXIT)
        else:
            event.app.invalidate()  # 立即重绘，让提示出现

    # 默认 InMemoryHistory：上下键回溯本次会话输入
    # bottom_toolbar：仅在提示窗口内显示「再按一次 Ctrl+C 退出」（返回 None 时隐藏）
    # refresh_interval：0.5s 重绘一次，提示 3 秒后自动过期消失
    prompt_session = PromptSession(
        message=[("class:prompt", "❯ ")],
        completer=NestedCompleter.from_nested_dict({cmd: None for cmd in COMMAND_NAMES}),
        key_bindings=kb,
        bottom_toolbar=lambda: _EXIT_HINT if tracker.hint_active() else None,
        refresh_interval=0.5,
    )

    # patch_stdout(raw=True)：Rich 输出与 prompt_toolkit 输入行共存
    with patch_stdout(raw=True):
        console = Console()
        _print_welcome(console, config)

        while True:
            try:
                text = prompt_session.prompt()
            except KeyboardInterrupt:
                continue  # 非按键来源的 SIGINT 安全网；按键 Ctrl+C 已被自定义绑定接管
            except EOFError:
                console.print("再见！👋")
                return
            if text is ExitSignal.EXIT:
                return  # 双击 Ctrl+C：正常退出，回到 CLI
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                result, feedback = handle_command(text, session, agent)
                console.print(feedback)
                if result is CommandResult.EXIT:
                    return
                continue
            _agent_round(console, agent, text, tracker)


def _agent_round(console: Console, agent: Agent, text: str, tracker: DoublePressTracker) -> None:
    """一轮 Agent Loop：后台跑 Agent，主线程消费事件渲染（F9）。

    用户消息不回显：prompt_toolkit 输入行已留在终端滚动区，问题天然可见。
    Ctrl+C 取消循环（已完成步骤保留）；取消后继续排空队列直到 RoundEnd。
    """
    q = run_in_background(agent, text)
    # 助手回复前缀独立成行，事件流内容渲染在它下方
    console.print(_ASSISTANT_PREFIX)
    usage: dict[str, int] = {"in": 0, "out": 0}
    end: RoundEnd | None = None
    try:
        end = _consume_events(console, q, usage)
    except KeyboardInterrupt:
        agent.cancel()
        tracker.reset()  # 双击退出计数从头开始
        try:
            end = _drain_cancelled(console, q, usage)
        except KeyboardInterrupt:
            end = None  # 用户再按 Ctrl+C：不再等待后台收尾
    if end is None:
        end = RoundEnd(RoundEndReason.CANCELLED, "已取消")
    _print_end(console, usage["in"], usage["out"], end)


def _consume_events(console: Console, q: queue.Queue, usage: dict[str, int]) -> RoundEnd:
    """消费事件队列直到 RoundEnd；返回结束事件。

    每轮一个 Live（流式文本 + 第 N 轮进度 + 思考计时），工具调用与
    结果在流结束后逐条打印（ch04 样式）。KeyboardInterrupt 向上传播
    给 _agent_round 处理取消；finally 保证 Live 一定关闭。
    """
    live: Live | None = None
    indicator: ThinkingIndicator | None = None
    iteration = 0
    thinking = ""
    reply: list[str] = []

    def render() -> None:
        if live is not None:
            live.update(
                _stream_renderable(thinking, "".join(reply), indicator, iteration)
            )

    def close_live() -> None:
        nonlocal live
        if live is not None:
            live.stop()
            live = None

    try:
        while True:
            try:
                event = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(event, ProgressEvent):
                # 新一轮：关闭上一轮 Live（工具面板已打印），开新 Live
                close_live()
                iteration = event.iteration
                thinking = ""
                reply.clear()
                indicator = ThinkingIndicator()
                live = Live(
                    indicator,
                    console=console,
                    refresh_per_second=10,
                    vertical_overflow="visible",
                )
                live.start()
                render()
            elif isinstance(event, ThinkingDelta):
                thinking += event.text
                render()
            elif isinstance(event, TextDelta):
                reply.append(event.text)
                render()
            elif isinstance(event, ToolCallStarted):
                close_live()
                _print_tool_call(console, event.call)
            elif isinstance(event, ToolResultEvent):
                _print_tool_result(console, event.result)
            elif isinstance(event, UsageEvent):
                usage["in"] = event.input_tokens
                usage["out"] = event.output_tokens
            elif isinstance(event, RoundEnd):
                if live is not None:
                    if event.reason is RoundEndReason.ERROR:
                        # 清空半截正文：错误面板上方不残留流式内容
                        live.update(Text(""), refresh=True)
                    live.stop()
                    live = None
                return event
    finally:
        close_live()


def _drain_cancelled(console: Console, q: queue.Queue, usage: dict[str, int]) -> RoundEnd | None:
    """取消后继续消费队列直到 RoundEnd（渲染途中到达的工具面板），超时放弃。"""
    deadline = time.monotonic() + _CANCEL_DRAIN_TIMEOUT
    while time.monotonic() < deadline:
        try:
            event = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if isinstance(event, ToolCallStarted):
            _print_tool_call(console, event.call)
        elif isinstance(event, ToolResultEvent):
            _print_tool_result(console, event.result)
        elif isinstance(event, UsageEvent):
            usage["in"] = event.input_tokens
            usage["out"] = event.output_tokens
        elif isinstance(event, RoundEnd):
            return event
    return None


def _print_end(console: Console, usage_in: int, usage_out: int, end: RoundEnd) -> None:
    """轮次收尾：token 用量行 + 停止原因面板。"""
    if usage_in or usage_out:
        console.print(
            Text(f"本轮 token: 输入 {usage_in} · 输出 {usage_out}", style=_TIMER_STYLE)
        )
    if end.reason is RoundEndReason.ERROR:
        console.print(
            Panel(end.message or "请求失败", title="[bold red]请求失败[/bold red]", border_style="red")
        )
    elif end.reason is RoundEndReason.CANCELLED:
        console.print(
            Panel(
                "已取消（已完成步骤保留）。",
                title="[bold yellow]已取消[/bold yellow]",
                border_style="yellow",
            )
        )
    elif end.reason is RoundEndReason.LIMIT:
        console.print(
            Panel(end.message, title="[bold yellow]已达迭代上限[/bold yellow]", border_style="yellow")
        )
    elif end.reason is RoundEndReason.UNKNOWN_TOOL:
        console.print(
            Panel(
                "连续调用未知工具，已停止。",
                title="[bold yellow]已停止[/bold yellow]",
                border_style="yellow",
            )
        )


def _print_tool_call(console: Console, call) -> None:
    """展示工具调用：工具名 + 参数摘要（F9）。"""
    args_summary = json.dumps(call.args, ensure_ascii=False)
    if len(args_summary) > _ARGS_SUMMARY_CHARS:
        args_summary = args_summary[: _ARGS_SUMMARY_CHARS] + "…"
    console.print(Text(f"🔧 {call.name} {args_summary}", style="dim"))


def _print_tool_result(console: Console, result) -> None:
    """展示工具结果面板（成功绿/失败红，按显示上限截断）。"""
    display = result.content
    if len(display) > _DISPLAY_RESULT_CHARS:
        display = display[: _DISPLAY_RESULT_CHARS] + f"\n…（终端显示截断，共 {len(result.content)} 字符）"
    if result.ok:
        console.print(
            Panel(display, title="[bold green]工具结果[/bold green]", border_style="green")
        )
    else:
        console.print(
            Panel(display, title="[bold red]工具失败[/bold red]", border_style="red")
        )


class ThinkingIndicator:
    """时间感知渲染对象：每次渲染时用单调时钟计算已思考秒数。

    rich Live 的 auto_refresh 线程会周期性重渲染当前 renderable，
    因此本对象无需额外线程即可让秒数自动跳动；clock 可注入便于测试。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def __rich_console__(self, console, options):
        """渲染灰色 thinking… Ns 计时行（N 为经过的整秒数）。"""
        elapsed = int(self._clock() - self._start)
        yield Text(f"thinking… {elapsed}s", style=_TIMER_STYLE)


def _stream_renderable(
    thinking: str, reply: str, indicator: ThinkingIndicator, iteration: int = 0
) -> Group:
    """组装 Live 渲染内容：第 N 轮 → 思考期灰色计时（有思考内容时并在下方）→ 流式正文。"""
    parts: list = []
    if iteration:
        parts.append(Text(f"第 {iteration} 轮", style=_TIMER_STYLE))
    if reply:
        parts.append(Markdown(reply))
    else:
        parts.append(indicator)
        if thinking:
            parts.append(Text(thinking, style="dim"))
    return Group(*parts)
