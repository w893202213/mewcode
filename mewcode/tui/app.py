"""TUI 主循环：输入、流式渲染、错误显示。

组合方式：prompt_toolkit PromptSession 管输入（历史/补全），Rich 管输出
（Markdown 渲染、Live 流式刷新、思考块灰色展示）。patch_stdout 让两者共存，
输入行始终停在最底部。
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from mewcode import __version__
from mewcode.config import ProviderConfig
from mewcode.provider.base import (
    Done,
    Provider,
    ProviderError,
    TextDelta,
    ThinkingDelta,
)
from mewcode.session import ChatSession
from mewcode.tui.commands import CommandResult, COMMAND_NAMES, handle_command

# 渲染样式常量
_USER_PREFIX = "[bold cyan]▸ 你[/bold cyan]"
_ASSISTANT_PREFIX = "[bold magenta]🐱 MewCode[/bold magenta]"
_THINKING_HEADER = "💭 思考中…"


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


def run(provider: Provider, config: ProviderConfig) -> None:
    """TUI 主循环：读输入 → 命令或对话 → 回到输入。"""
    session = ChatSession()
    # 默认 InMemoryHistory：上下键回溯本次会话输入
    prompt_session = PromptSession(
        message=[("class:prompt", "❯ ")],
        completer=NestedCompleter.from_nested_dict({cmd: None for cmd in COMMAND_NAMES}),
    )

    # patch_stdout(raw=True)：Rich 输出与 prompt_toolkit 输入行共存
    with patch_stdout(raw=True):
        console = Console()
        _print_welcome(console, config)
        while True:
            try:
                text = prompt_session.prompt()
            except KeyboardInterrupt:
                continue  # Ctrl+C：放弃当前输入行，继续
            except EOFError:
                console.print("再见！👋")
                return
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                result, feedback = handle_command(text, session)
                console.print(feedback)
                if result is CommandResult.EXIT:
                    return
                continue
            _chat_round(console, provider, session, text)


def _chat_round(console: Console, provider: Provider, session: ChatSession, text: str) -> None:
    """一轮对话：打印用户消息 → 流式渲染回复 → 落盘会话；失败回滚并显示错误。"""
    console.print(f"{_USER_PREFIX} {text}")
    start_index = len(session.messages)  # 记录起点，失败时回滚
    session.add_user(text)

    reply_buffer: list[str] = []
    thinking_text = ""
    thinking_raw: list[dict] = []

    try:
        # 助手回复前缀独立成行，流式内容渲染在它下方
        console.print(_ASSISTANT_PREFIX)
        # Live 区域：思考区（灰色）+ 流式 Markdown；输入行由 patch_stdout 保持在底部
        with Live(
            Markdown(""), console=console, refresh_per_second=10, vertical_overflow="visible"
        ) as live:
            for event in provider.stream(session.messages):
                if isinstance(event, ThinkingDelta):
                    thinking_text += event.text
                    live.update(_thinking_renderable(thinking_text, "".join(reply_buffer)))
                elif isinstance(event, TextDelta):
                    reply_buffer.append(event.text)
                    live.update(_thinking_renderable(thinking_text, "".join(reply_buffer)))
                elif isinstance(event, Done):
                    thinking_raw = event.thinking_raw
        session.add_assistant(
            text="".join(reply_buffer),
            thinking=thinking_text,
            thinking_raw=thinking_raw,
        )
    except ProviderError as e:
        # 回滚本轮 user 消息：未被处理的输入不留在上下文里
        del session.messages[start_index:]
        console.print(Panel(str(e), title="[bold red]请求失败[/bold red]", border_style="red"))


def _thinking_renderable(thinking: str, reply: str) -> Group:
    """组装 Live 渲染内容：思考区（灰色）在上、Markdown 回复在下。"""
    if thinking:
        return Group(
            Text(f"{_THINKING_HEADER}\n{thinking}", style="dim"),
            Markdown(reply),
        )
    return Group(Markdown(reply))
