"""分发器：解析结果 → 处理函数，统一未命中提示（ch12 F3/F5）。

命令按类型（本地/界面/提示词）分类只是元数据标记，分发统一走
处理函数；处理函数返回 CommandResult 决定输入循环继续还是退出。
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from mewcode.commands.parser import ParsedCommand
from mewcode.commands.ui import UiController

if TYPE_CHECKING:
    from mewcode.commands.registry import CommandRegistry


class CommandResult(Enum):
    """命令执行结果：继续输入循环还是退出 TUI。"""

    CONTINUE = auto()
    EXIT = auto()


def dispatch(registry: CommandRegistry, parsed: ParsedCommand, ui: UiController) -> CommandResult:
    """分发一条解析结果；未命中或无处理函数时显示未知命令提示（带 /help 引导）。"""
    spec = registry.resolve(parsed.name)
    if spec is None or spec.handler is None:
        ui.display(f"未知命令: /{parsed.name}（输入 /help 查看可用命令）")
        return CommandResult.CONTINUE
    result = spec.handler(parsed, ui)
    return result if result is not None else CommandResult.CONTINUE
