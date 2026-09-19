"""斜杠命令：解析与分发。"""

from __future__ import annotations

from enum import Enum, auto

from mewcode.session import ChatSession


class CommandResult(Enum):
    """命令执行结果：继续主循环还是退出。"""

    CONTINUE = auto()
    EXIT = auto()


# 可用命令列表（供补全与 /help 展示）
COMMAND_NAMES = ["/exit", "/clear", "/help"]

_HELP_TEXT = "\n".join(
    [
        "可用命令：",
        "  /exit   退出 MewCode",
        "  /clear  清空对话上下文，重新开始",
        "  /help   显示本帮助",
        "输入历史可用上下方向键回溯。",
    ]
)


def handle_command(text: str, session: ChatSession) -> tuple[CommandResult, str]:
    """处理斜杠命令，返回（结果，反馈文本）。

    反馈文本由 TUI 显示，可能是命令说明、清空提示或未知命令提示。
    """
    command = text.strip()
    if command == "/exit":
        return CommandResult.EXIT, "再见！👋"
    if command == "/clear":
        session.clear()
        return CommandResult.CONTINUE, "已清空对话上下文，重新开始。"
    if command == "/help":
        return CommandResult.CONTINUE, _HELP_TEXT
    return CommandResult.CONTINUE, f"未知命令: {command}（输入 /help 查看可用命令）"
