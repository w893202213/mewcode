"""TUI 主循环：输入、流式渲染、错误显示。

组合方式：prompt_toolkit PromptSession 管输入（历史/补全），Rich 管输出
（Markdown 渲染、Live 流式刷新、思考块灰色展示）。patch_stdout 让两者共存，
输入行始终停在最底部。
"""

from __future__ import annotations

import json
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
from mewcode.agent import ToolRunner
from mewcode.config import ProviderConfig
from mewcode.provider.base import (
    Done,
    Provider,
    ProviderError,
    TextDelta,
    ThinkingDelta,
    ToolCall,
)
from mewcode.session import ChatSession, ToolUse
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
    """TUI 主循环：读输入 → 命令或对话 → 回到输入。"""
    session = ChatSession()

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

        # 命令执行确认：先展示完整命令，再等用户输入 y/n；空输入与 Ctrl+C 一律拒绝（安全默认）
        confirm_session = PromptSession(
            message=[("class:prompt", "执行命令? [y/N] ")],
        )

        def confirm(command: str) -> bool:
            console.print(Text(f"$ {command}", style="bold"))
            try:
                answer = confirm_session.prompt().strip().lower()
            except (KeyboardInterrupt, EOFError):
                console.print("（已取消，视为拒绝）")
                return False
            return answer in ("y", "yes")

        runner = ToolRunner(registry, session, confirm=confirm)
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
                result, feedback = handle_command(text, session)
                console.print(feedback)
                if result is CommandResult.EXIT:
                    return
                continue
            _chat_round(console, provider, session, registry, runner, text, tracker)


def _chat_round(
    console: Console,
    provider: Provider,
    session: ChatSession,
    registry: ToolRegistry,
    runner: ToolRunner,
    text: str,
    tracker: DoublePressTracker,
) -> None:
    """一轮对话：流式渲染回复 → 落盘会话；失败或中断回滚并提示。

    用户消息不回显：prompt_toolkit 输入行已留在终端滚动区，问题天然可见。
    """
    start_index = len(session.messages)  # 记录起点，失败时回滚
    session.add_user(text)

    reply_buffer: list[str] = []
    thinking_text = ""
    thinking_raw: list[dict] = []
    # 工具调用：本步每轮只执行第一个，其余计数提示忽略（F8）
    first_call: ToolCall | None = None
    ignored_calls = 0

    try:
        # 助手回复前缀独立成行，流式内容渲染在它下方
        console.print(_ASSISTANT_PREFIX)
        # 计时起点：请求即将发出；静默期由 Live 的 auto_refresh 驱动秒数跳动
        indicator = ThinkingIndicator()
        # Live 区域：思考期灰色计时（+思考文本）+ 流式 Markdown；输入行由 patch_stdout 保持在底部
        with Live(
            indicator, console=console, refresh_per_second=10, vertical_overflow="visible"
        ) as live:
            try:
                for event in provider.stream(session.messages, registry.list()):
                    if isinstance(event, ThinkingDelta):
                        thinking_text += event.text
                        live.update(
                            _stream_renderable(thinking_text, "".join(reply_buffer), indicator)
                        )
                    elif isinstance(event, TextDelta):
                        reply_buffer.append(event.text)
                        live.update(
                            _stream_renderable(thinking_text, "".join(reply_buffer), indicator)
                        )
                    elif isinstance(event, ToolCall):
                        if first_call is None:
                            first_call = event
                        else:
                            ignored_calls += 1
                    elif isinstance(event, Done):
                        thinking_raw = event.thinking_raw
            except (ProviderError, KeyboardInterrupt):
                # 清空 Live 区域：错误/中断面板上方不残留计时行或半截正文
                live.update(Text(""), refresh=True)
                raise
        if first_call is not None:
            # 工具轮次：记录 assistant 消息（含工具调用），执行并展示结果
            session.add_assistant(
                text="".join(reply_buffer),
                thinking=thinking_text,
                thinking_raw=thinking_raw,
                tool_uses=[ToolUse(id=first_call.id, name=first_call.name, args=first_call.args)],
            )
            _run_tool_round(console, runner, first_call, ignored_calls)
        else:
            session.add_assistant(
                text="".join(reply_buffer),
                thinking=thinking_text,
                thinking_raw=thinking_raw,
            )
    except ProviderError as e:
        # 回滚本轮 user 消息：未被处理的输入不留在上下文里
        del session.messages[start_index:]
        console.print(Panel(str(e), title="[bold red]请求失败[/bold red]", border_style="red"))
    except KeyboardInterrupt:
        # 流式中断（Ctrl+C）：回滚未完成的轮次，回到输入行；双击计数从头开始
        del session.messages[start_index:]
        tracker.reset()
        console.print(
            Panel(
                "已中断，本轮回复未保存。",
                title="[bold yellow]回复中断[/bold yellow]",
                border_style="yellow",
            )
        )


def _run_tool_round(
    console: Console, runner: ToolRunner, call: ToolCall, ignored_calls: int
) -> None:
    """展示工具调用 → 执行（含确认拦截）→ 展示结果（F9）。

    结果面板按显示上限截断；回灌会话的内容由 run_tool 按 30000 字符上限截断，
    两者互不影响。
    """
    args_summary = json.dumps(call.args, ensure_ascii=False)
    if len(args_summary) > _ARGS_SUMMARY_CHARS:
        args_summary = args_summary[: _ARGS_SUMMARY_CHARS] + "…"
    console.print(Text(f"🔧 {call.name} {args_summary}", style="dim"))
    if ignored_calls:
        console.print(
            Text(f"另有 {ignored_calls} 个工具调用本步忽略（每轮只执行一个工具）", style="dim")
        )

    result = runner.run(call)
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
    thinking: str, reply: str, indicator: ThinkingIndicator
) -> Group:
    """组装 Live 渲染内容：思考期显示灰色计时行（有思考内容时并在下方），回复开始后只显示正文。"""
    if reply:
        return Group(Markdown(reply))
    if thinking:
        return Group(indicator, Text(thinking, style="dim"))
    return Group(indicator)
